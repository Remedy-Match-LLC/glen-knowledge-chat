"""Pending health-record edits surfaced from the portal chat (or entered by a
clinician) for the client to review and approve before they land in their
intake record. Pure sqlite; caller passes the connection (mirrors
dashboard/portal_chat.py and the other dashboard/*.py stores).

Two producers write into this queue:
  - the portal chat hook (app.py, beside portal_chat.record_exchange): best-
    effort LLM extraction of a chat turn into candidate field edits.
  - a clinician, entering a suggested edit directly (source='clinician').

Both feed the same table; `list_pending`/`count_pending` drive the "My Health
Profile" suggestion badge (see health_profile.build_block), and `resolve`
is how a client accepts/edits/dismisses a suggestion.
"""
import json
import re
import sqlite3
from datetime import datetime, timezone

_VALID_STATUSES = ("confirmed", "edited", "dismissed")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(email):
    return (email or "").strip().lower()


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS health_suggestions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        source TEXT,
        source_msg_id INTEGER,
        field_id TEXT,
        suggested_value TEXT,
        rationale TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        resolved_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_hs_email_status "
               "ON health_suggestions(email, status)")
    # Partial UNIQUE index: only pending rows collide, so a re-mention of the
    # same field/value while a suggestion is still pending is a no-op (INSERT
    # OR IGNORE below), but the same field/value can be re-suggested later
    # once the prior row has been resolved (it's no longer in the partial
    # index's row set).
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hs_pending_dedupe "
               "ON health_suggestions(email, field_id, suggested_value) "
               "WHERE status='pending'")
    cx.commit()


def add_pending(cx, email, field_id, value, rationale, source, source_msg_id=None):
    """Queue one candidate field edit as a pending suggestion. INSERT OR IGNORE
    against the partial unique index so a re-mention of the same (email,
    field_id, value) while a suggestion is still pending does not stack a
    duplicate row. `source` is 'chat' or 'clinician'. `suggested_value` is
    stored as a string (non-str values are JSON-encoded). Returns the new row
    id, or None if the insert was ignored (duplicate) or email/field_id was
    empty."""
    init_table(cx)
    e = _norm(email)
    fid = (field_id or "").strip()
    if not e or not fid:
        return None
    val = value if isinstance(value, str) else json.dumps(value)
    # RETURNING id (not cur.lastrowid): the Postgres adapter raises on
    # `lastrowid`, and the translator splices ON CONFLICT DO NOTHING before a
    # trailing RETURNING, so this yields the new id on insert and no row when the
    # partial-unique index ignores a duplicate -- identical on SQLite and Postgres.
    cur = cx.execute(
        "INSERT OR IGNORE INTO health_suggestions "
        "(email, source, source_msg_id, field_id, suggested_value, rationale, "
        " status, created_at) VALUES (?,?,?,?,?,?,'pending',?) RETURNING id",
        (e, source, source_msg_id, fid, val, rationale, _now()))
    row = cur.fetchone()
    cx.commit()
    return row[0] if row else None


def list_pending(cx, email):
    """The client's pending suggestions, oldest first."""
    init_table(cx)
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT id, source, field_id, suggested_value, rationale FROM health_suggestions "
        "WHERE email=? AND status='pending' ORDER BY id", (_norm(email),)).fetchall()
    return [{"id": r["id"], "source": r["source"], "field_id": r["field_id"],
             "suggested_value": r["suggested_value"], "rationale": r["rationale"]}
            for r in rows]


def count_pending(cx, email):
    init_table(cx)
    row = cx.execute(
        "SELECT COUNT(*) FROM health_suggestions WHERE email=? AND status='pending'",
        (_norm(email),)).fetchone()
    return row[0] if row else 0


def resolve(cx, sug_id, email, status):
    """Resolve one of the client's own pending suggestions (confirmed/edited/
    dismissed), stamping resolved_at. Scoped to `email` so a client can't
    resolve another client's row. No-op (returns False) if the id doesn't
    belong to that email or `status` isn't one of the valid values."""
    init_table(cx)
    if status not in _VALID_STATUSES:
        return False
    cur = cx.execute(
        "UPDATE health_suggestions SET status=?, resolved_at=? WHERE id=? AND email=?",
        (status, _now(), sug_id, _norm(email)))
    cx.commit()
    return cur.rowcount == 1


# --- LLM extractor (Glen's rulings, 20 Oct 2026) ------------------------------
# Glen, 20 Oct 2026: record "any condition or surgery a client mentions... Also
# symptoms, supplements, medications... anything that can be added to their Intake
# form", always as a suggestion the client confirms on the portal "Suggested
# updates" card. Facts are the client's own, stated in their own message. The one
# exception is family_history, where a blood relative's condition is the point.
# Placement: "pregnancy or breastfeeding can go in as current health conditions.
# Implants and pacemakers fit in surgeries. Heavy drinking and medical diet go in
# diet", with diet and alcohol as two separate fields.
#
# Left out on purpose: identity and contact fields, the 1-10 scales and ratings
# (supplements.need, health_concerns.rating), budget, commitment, consent and
# portrait; psychoemotional_trauma (sensitive); systemic_symptoms (a fixed
# multi-choice list, and a confirmed value would replace the client's picks:
# symptoms go to other_symptoms instead); obstacles.

# field_id -> its row columns, primary column first. A row needs the primary, and
# the primary's words must appear in the client's quote.
TABLE_COLUMNS = {
    "diagnoses": ("diagnosis", "current", "age_onset"),
    "surgeries": ("procedure", "reason", "age"),
    "medications": ("medication", "reason"),
    "otc_drugs": ("medication", "reason"),
    "supplements": ("name", "brand", "reason"),
    "allergies": ("sensitivity", "reaction"),
    "family_history": ("condition", "relative", "age_onset"),
    "health_concerns": ("concern",),
}
# Free text: the client's own words are what gets recorded, appended as a new line.
TEXT_FIELDS = ("diet", "alcohol", "other_symptoms", "sleep", "dental", "toxins",
               "physical_trauma", "vaccinations")
EXTRACT_FIELDS = tuple(TABLE_COLUMNS) + TEXT_FIELDS
# These ask about now, so a symptom or sleep trouble the client says is over is not
# recorded.
_CURRENT_ONLY_TEXT = ("other_symptoms", "sleep")
# The form asks what the client takes now (round 1 H1).
_CURRENT_ONLY_TABLES = ("medications", "otc_drugs", "supplements")

EXTRACT_SYSTEM = (
    "You read ONE message a client wrote in their health portal chat. Find only facts the "
    "client states about THEMSELF, as true now or in their past, that belong on their health "
    "intake form. Family history is the one exception, below.\n"
    "- any medical condition or diagnosis, current or past -> field \"diagnoses\", value "
    "{\"diagnosis\": name, \"current\": \"Current\", or \"Past\" only when the client "
    "says it is in the past, \"age_onset\": their age when it began, only if stated}. One "
    "item per condition: \"I have type 2 diabetes and high blood pressure\" is two items. A "
    "CURRENT pregnancy or breastfeeding is a condition: {\"diagnosis\": \"Pregnancy\" or "
    "\"Breastfeeding\", \"current\": \"Current\"}. Never a past pregnancy.\n"
    "- any surgery, operation, procedure, hospital stay, or implant (a pacemaker, a hip) -> "
    "field \"surgeries\", value {\"procedure\": what was done, \"reason\": why if stated, "
    "\"age\": their age then if stated}\n"
    "- a prescription medicine -> field \"medications\", value {\"medication\": name, "
    "\"reason\": what for if stated}\n"
    "- a non-prescription drug (a pain reliever, an antacid, an antihistamine) -> field "
    "\"otc_drugs\", value {\"medication\": name, \"reason\": what for if stated}\n"
    "- a supplement (a vitamin, mineral, herb, probiotic, oil or remedy) -> field "
    "\"supplements\", value {\"name\": the supplement, \"brand\": brand if stated, "
    "\"reason\": what for if stated}\n"
    "- any allergy or sensitivity (a food, pollen, mould, a drug, a chemical) -> field "
    "\"allergies\", value {\"sensitivity\": what they react to, \"reaction\": the "
    "reaction if stated}\n"
    "- a health goal the client states for themself (\"I want to get rid of my migraines\") "
    "-> field \"health_concerns\", value {\"concern\": the goal or concern in their words}\n"
    "- a symptom the client has now -> field \"other_symptoms\"\n"
    "- trouble falling asleep, staying asleep or waking -> field \"sleep\"\n"
    "- dental work or problems (amalgam fillings, root canals, implants in the mouth) -> field "
    "\"dental\"\n"
    "- an exposure to a toxin (mould, pesticides, metals, solvents, smoke) -> field "
    "\"toxins\"\n"
    "- a physical injury or accident (a fall, a fracture, a car accident, a concussion) -> "
    "field \"physical_trauma\"\n"
    "- a vaccination -> field \"vaccinations\"\n"
    "- a medical or special diet -> field \"diet\"\n"
    "- heavy or regular drinking of alcohol -> field \"alcohol\"\n"
    "For every free-text field above (other_symptoms, sleep, dental, toxins, physical_trauma, "
    "vaccinations, diet, alcohol) the quote is what gets recorded, so quote the whole fact.\n"
    "- FAMILY HISTORY: a condition a blood relative of the client has or had (\"my mother "
    "had breast cancer\") -> field \"family_history\", value {\"relative\": who (Mother, "
    "Father, Sister, Grandmother...), \"condition\": the condition, \"age_onset\": the "
    "relative's age when it began, only if stated}, and \"about\" is \"family\". Only the "
    "client's OWN blood relatives. Blood relatives are parents, children, siblings, "
    "half-siblings, grandparents, grandchildren, aunts, uncles, nieces, nephews and cousins. "
    "A parent's sibling (\"my mother's brother\" is an uncle) or a parent's parent (\"my "
    "dad's mother\" is a grandmother) IS a blood relative: record it. Spouses, partners, "
    "in-laws, step-relatives and adoptive relatives are NOT blood relatives, and never a "
    "friend, neighbour, coworker, client, patient, or anyone's relative but the client's.\n"
    "Never record anything else about another person. Never guess or infer. Leave a value "
    "column blank unless the client said it: never fill in a reason, brand, reaction or age "
    "they did not state.\n"
    "A fact the client states inside a question is still a fact. In \"Can I take this while "
    "I'm on warfarin?\", \"Is the mat safe since I have a pacemaker?\", \"Is it ok because I "
    "take metformin?\" and \"I take warfarin, can I use the enzymes?\" the client IS on "
    "warfarin, HAS a pacemaker and DOES take metformin: record those. Only a fact under if, "
    "whether, suppose or what if is hypothetical and is not recorded (\"is it safe if I were "
    "pregnant?\", \"does breast cancer run in families?\").\n"
    "Never record something the client does not have, only suspects, has stopped, or only "
    "plans (\"I don't have diabetes\", \"I might have Lyme\", \"I stopped taking "
    "warfarin\", \"I need a hip replacement\"). A past condition is recorded as Past.\n"
    "Leave out anything not on this list, and never record feelings, emotional trauma, stress, "
    "budget, ratings or contact details. Physical injuries are recorded as above. For every "
    "item, \"quote\" is the client's exact words that state it, copied from their message: "
    "at least 3 words, with I or my (my or our for family history), and the name you put in "
    "the value. Quote only the client's own clause: \"I have high blood pressure\" from \"I "
    "have high blood pressure like my dad\". Copy the quote EXACTLY as the client wrote it, "
    "never reworded and never with words added. For a second fact joined by \"and\", quote "
    "only the words the client wrote: from \"I have type 2 diabetes and high blood "
    "pressure\" the second quote is \"high blood pressure\", not \"I have high blood "
    "pressure\". \"about\" is \"self\" (\"family\" for family history).\n"
    "Return STRICT JSON ONLY, no prose: {\"items\": [{\"field_id\": ..., \"value\": ..., "
    "\"quote\": ..., \"about\": ...}]}. With nothing to record return {\"items\": []}."
)


def _strict_json(text):
    """json.loads of the whole reply (a ```json fence is allowed, nothing else).
    Anything else is garbage -> None."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t[3:]
        if t.lower().startswith("json"):
            t = t[4:]
        if not t.rstrip().endswith("```"):
            return None
        t = t.rstrip()[:-3]
    try:
        return json.loads(t)
    except (TypeError, ValueError):
        return None


def _squash(text):
    return " ".join(str(text or "").split()).casefold()


def _cell(value, limit=200):
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return " ".join(str(value).split())[:limit]


def normalize_row(field_id, value):
    """A row dict for a table field, holding only that field's column ids, or None
    when the primary column is empty. diagnoses.current is 'Current' or 'Past'."""
    cols = TABLE_COLUMNS.get(field_id)
    if not cols or not isinstance(value, dict):
        return None
    row = {}
    for col in cols:
        cell = _cell(value.get(col))
        if col == "current":
            cell = "Past" if cell.casefold() == "past" else "Current"   # _normalize_item may revise
        elif col in ("age", "age_onset"):
            try:
                cell = int(float(cell)) if cell else ""
            except (ValueError, OverflowError):
                cell = ""                # "inf" or "nan" from the model (round 1 L2)
            if cell != "" and not 0 <= cell <= 120:
                cell = ""
        if cell != "":
            row[col] = cell
    return row if row.get(cols[0]) else None


# Someone else. A quote, or the sentence it came from, naming one is not the
# client's own fact, whatever the model says in "about".
# Round 3 H1 widened it to spouses, in-laws and step-relatives, plural family words,
# young children, pets, and the pronouns he, she and they.
_OTHER_PERSON = re.compile(
    r"\b(?:wi(?:fe|fes|ves)|wifey|husbands?|hubby|spouses?|fianc[eé]e?s?|partners?|"
    r"boyfriends?|girlfriends?|daughters?|sons?|step\w*|\w+-in-laws?|in-laws?|"
    r"moms?|mums?|mothers?|dads?|fathers?|parents?|sisters?|brothers?|siblings?|twins?|"
    r"bab(?:y|ies)|toddlers?|teens?|teenagers?|child|childs|children|kids?|"
    r"grand(?:ma|pa|mother|father|son|daughter|parent|child|kid)s?|grandchildren|"
    r"aunts?|uncles?|cousins?|nieces?|nephews?|friends?|neighbou?rs?|co-?workers?|"
    r"colleagues?|room-?mates?|boss(?:es)?|clients?|patients?|"
    r"dogs?|cats?|pets?|pupp(?:y|ies)|kittens?|horses?|"
    # Not they or them: "I still get them" is about the client's own migraines.
    r"he|she|him|his|her|hers)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i've|ive|i'd|i'll|my|me|myself|mine)\b", re.I)
# The present perfect ("I've had shingles twice") is past unless something says it
# goes on ("since", "for", "still", "every week") or it is ongoing ("I've been
# sleeping badly", "I've been on warfarin", "I've been diagnosed"). Round 3 M2.
_PERFECT = re.compile(r"\b(?:i've|ive|i have|have|has)\s+(?:had|been)\b", re.I)
_PERFECT_GOES_ON = re.compile(r"\s+(?:\w+ing|on|diagnosed|told)\b", re.I)
_ONGOING = re.compile(r"\b(?:since|for|still|now|currently|lately|these days|nowadays|every|"
                      r"this (?:week|month|year)|all my life|my whole life|always|on and off)\b",
                      re.I)
_PAST = re.compile(
    r"\b(?:was|were|had|used to|ago|previously|formerly|back in|last year|as a (?:child|kid)|"
    r"in (?:19|20)\d\d|no longer|anymore|any more)\b|\b\w+ed\b(?= (?:in|at|when|back|before))",
    re.I)
_PREGNANCY = re.compile(r"\b(?:pregnan\w*|breast-?feed\w*|breastfed|nursing)\b", re.I)


def _source_sentence(quote, client_msg):
    q = _squash(quote)
    for sent in re.split(r"(?<=[.!?])\s+|\n+", client_msg or ""):
        if q and q in _squash(sent):
            return sent
    return client_msg or ""


# A fact stated before a question in the same sentence is still a fact (marketing's
# live run, 20 Oct 2026): "I take warfarin, can I use the enzymes?". The quote's
# own clause must open plainly, and what follows it must be the question.
_ASKING = re.compile(r"\b(?:if|whether|when|what|how|why|which|is|are|am|can|could|should|"
                     r"would|will|do|does|did|may|might|possible|suppose|supposing)\b", re.I)
_SUPPOSING = re.compile(r"\b(?:if|whether|suppose|supposing|what if|in case|were)\b", re.I)
_QUESTION_AFTER = re.compile(r"^[\s,;:.!-]*(?:(?:and|but|so|then)\s+)?(?:is|are|am|can|could|"
                             r"should|would|will|do|does|did|may|might|what|how|which|"
                             r"any|anything)\b", re.I)


def _asked(quote, sentence):
    """True when the quote sits inside the sentence's question rather than before it."""
    if not sentence.rstrip().endswith("?"):
        return False
    flat, q = _squash(sentence), _squash(quote)
    at = flat.find(q)
    if at < 0:
        return True
    before = re.split(r"[,;:]", flat[:at])[-1]
    after = flat[at + len(q):]
    # "Can I take this while I'm on warfarin?" asks about a fact it also states, so a
    # stating link straight before the quote keeps it (marketing's live run, 20 Oct).
    # The link may also open the quote ("while I'm on warfarin"), live run 20 Oct.
    link = r"\b(?:while|since|as|because|given(?: that)?|now that)"
    stated = re.search(link + r"\s*$", before) or re.match(link + r"\b", q)
    if _SUPPOSING.search(flat[:at]):
        return True                      # "is it safe if, while I'm pregnant, I fast?"
    if stated:
        return False
    if _ASKING.search(before):
        return True                      # "is it safe if I am pregnant?"
    if re.match(r"^\s*,?\s*(?:right|correct|true|yes|no|or not|isn['’]?t it|don['’]?t i|"
                r"is that right|am i right)\s*\?", after):
        return True                      # "I have candida, right?" (round 3 L2)
    # A clause break after the quote ends the fact ("I take warfarin and metformin,
    # can I..."); with none, the question must start straight after it.
    return not re.search(r"[,;:]", after) and not _QUESTION_AFTER.search(after)


def _clauses(text):
    """A text split at commas, semicolons and joining words, blanks dropped."""
    return [c for c in re.split(r"[,;:]|\b(?:and|but|so|which|who|although|though|whereas)\b",
                                text or "", flags=re.I) if c.strip()]


# --- tense (review round 1, H1 H2 M3) ------------------------------------------
# A date of diagnosis or onset says when it began, not that it ended. So an ended
# marker decides Past, then a present marker decides Current, and only then does a
# past-tense word count. "I was diagnosed with diabetes last year" is Current.
_ENDED = re.compile(
    r"\b(?:no longer|not any ?more|anymore|any more|used to|stopped|quit|came off|come off|"
    r"went off|gone off|gave up|given up|discontinued|resolved|cured|gone|went away|"
    r"got over|recovered|in remission|outgrew|grew out of|did until|until recently|"
    r"until last \w+)\b", re.I)
_PRESENT = re.compile(
    r"\b(?:have|has|i've|ive|i'm|im|am|is|are|now|still|currently|since|lately|these days|"
    r"nowadays|diagnosed|started|began|developed|born with)\b", re.I)


def _is_past(quote, term=None, trail=""):
    """True when the quote says the fact is over. With a term, the clause that names
    it decides ("I had a hip replacement and now have arthritis": arthritis is now),
    and an ended marker in that clause or after it wins. `trail` is the sentence after
    the quote ("I take aspirin but stopped last week"), read for an ended marker and
    for "still"."""
    parts = _clauses(quote) or [quote]
    at = 0
    if term:
        words = _significant(term)
        toks = [_tokens(c) for c in parts]
        at = next((i for i, t in enumerate(toks) if any(_word_supported(w, t) for w in words)), 0)
        own, rest = parts[at], " ".join(parts[at:])
    else:
        own = rest = quote
    rest = rest + " " + (trail or "")
    if _ENDED.search(rest):
        return True
    if re.search(r"\bstill\b", rest, re.I):
        return False                     # "I got Lyme in 2015 and still have symptoms"
    perfect = _PERFECT.search(own)
    if perfect:
        return not (_PERFECT_GOES_ON.match(own, perfect.end()) or _ONGOING.search(own))
    if _PRESENT.search(own):
        return False
    return bool(_PAST.search(own))


# --- evidence: words the client actually said (round 2) ------------------------
_STOP_WORDS = frozenset(("a", "an", "the", "and", "of", "for", "with", "in", "on", "to", "my",
                         "or"))
# A key term may carry these without the client saying them: "Knee surgery" from "I
# had my knee operated on". At least one other word must remain.
_ALWAYS_GENERIC = frozenset(("surgery", "surgical", "operation", "procedure", "op",
                             "implant", "implantation", "insertion", "placement"))
# "Gallbladder removal" is supported only when the client said it came out.
_REMOVAL_WORDS = frozenset(("removal", "removed", "removing", "excision"))
_SAYS_REMOVED = re.compile(r"\b(?:out|removed|removal|excised)\b", re.I)


def _tokens(text):
    flat = re.sub(r"['’]s\b", "", str(text or "").casefold())
    return re.findall(r"[a-z0-9]+", flat)


def _significant(text):
    return [w for w in _tokens(text) if w not in _STOP_WORDS]


def _shared(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _word_supported(word, toks):
    """A short word or one with a digit ("C", "B12", "D3", "2") must be said exactly.
    A longer word may differ from the client's only in its last 3 letters, so
    "Pregnancy" fits "pregnant" and "Anemia" fits "anemic", but "Osteoporosis" does
    not fit "osteopenia" (round 3 M4)."""
    if len(word) <= 3 or any(ch.isdigit() for ch in word):
        return word in toks
    return any(_shared(word, t) >= max(4, max(len(word), len(t)) - 3) for t in toks)


# A small synonym list for the key-term check only (live run, 20 Oct 2026). A key
# term on the left is also supported when the quote says one of these instead.
_TERM_SYNONYMS = {
    # "nursing" but not "nursing a cold": after a/an/the it needs a baby word.
    # "breastfeed" and "breastfeeds" already match by the word rule in _word_supported.
    "breastfeeding": (r"nursing(?!\s+(?:a|an|the)\s+(?!bab|child|newborn|little))",
                      r"breast[ -]feeding"),
    # "expecting" only with a baby or child word, as in the own-baby rule.
    "pregnancy": (r"pregnant", r"expecting\s+(?:(?:my|our|a|an)\s+)?(?:\w+\s+)?"
                               r"(?:bab(?:y|ies)|child|twins|little one)"),
    "high blood pressure": (r"hypertension", r"high bp"),
    "irritable bowel syndrome": (r"ibs",),
}


def _term_in_quote(term, quote):
    """Every significant word of the key term is in the quote. Only the generic
    surgery words, and removal words when the client said it came out, are spared."""
    for alt in _TERM_SYNONYMS.get(" ".join(_tokens(term)), ()):
        if re.search(r"\b" + alt + r"\b", quote, re.I):
            return True
    toks = _tokens(quote)
    removed = bool(_SAYS_REMOVED.search(quote))
    need = [w for w in _significant(term)
            if w not in _ALWAYS_GENERIC and not (removed and w in _REMOVAL_WORDS)]
    return bool(need) and all(_word_supported(w, toks) for w in need)


def _cell_supported(text, client_msg):
    """A reason, brand or reaction is kept only when the client said its words."""
    words = _significant(text)
    toks = _tokens(client_msg)
    return bool(words) and all(_word_supported(w, toks) for w in words)


def _age_stated(age, client_msg):
    """An age is kept only when the message ties that number to an age: "at 34", "age
    34", "when I was 34", "34 years old". A dose or a duration is not an age."""
    n = re.escape(str(age))
    tied = (r"\b(?:at(?:\s+the)?(?:\s+age(?:\s+of)?)?|aged?(?:\s+of)?|"
            r"when\s+(?:i|he|she|they)\s+(?:was|were)|(?:i|he|she)\s+was)\s+" + n +
            r"\b(?!\s*(?:am|pm|a\.m|p\.m|o'?clock|mg|mcg|g|ml|%|:|percent|times|days?|weeks?|"
            r"months?|lbs?|kg|pounds)\b)")
    old = r"\b" + n + r"(?:\s*-\s*|\s+)(?:years?|yrs?)(?:\s*-\s*|\s+)old\b|\b" + n + r"\s*(?:yo|y/o)\b"
    return bool(re.search(tied, client_msg or "", re.I) or re.search(old, client_msg or "", re.I))


# --- family history (Glen, 20 Oct 2026; rounds 1 and 2) ------------------------
# The client's OWN blood relatives only. Each spelling maps to one canonical
# relative, so "Mother" in the value is supported by "my mom" in the quote.
_RELATIVES = {
    "mother": "mother", "mom": "mother", "mum": "mother", "mama": "mother", "mommy": "mother",
    "father": "father", "dad": "father", "papa": "father", "daddy": "father",
    "parent": "parent", "parents": "parent",
    "sister": "sister", "sisters": "sister", "brother": "brother", "brothers": "brother",
    "sibling": "sibling", "siblings": "sibling",
    "grandmother": "grandmother", "grandma": "grandmother", "granny": "grandmother",
    "grandfather": "grandfather", "grandpa": "grandfather",
    "grandparent": "grandparent", "grandparents": "grandparent",
    "aunt": "aunt", "aunts": "aunt", "uncle": "uncle", "uncles": "uncle",
    "cousin": "cousin", "cousins": "cousin", "niece": "niece", "nephew": "nephew",
    "son": "son", "sons": "son", "daughter": "daughter", "daughters": "daughter",
    "child": "child", "children": "child", "kid": "child", "kids": "child",
}
_RELATIVE_WORD = "(?:" + "|".join(sorted(_RELATIVES, key=len, reverse=True)) + ")"
# Only these words may sit between "my" and the relative. Anything else ("adoptive",
# "foster", "step", "doctors", "friends") means it is not the client's blood relative.
_FAMILY_MODIFIERS = ("late", "older", "younger", "eldest", "oldest", "elder", "youngest",
                     "little", "big", "baby", "biological", "birth", "maternal", "paternal",
                     "own", "twin", "half", "great", "dear")
_MOD = r"(?:(?:" + "|".join(_FAMILY_MODIFIERS) + r")(?:\s+|-))"
# "my mother", "our late father", "my mother's brother": the chain's last link is who.
_REL_CHAIN = re.compile(r"\b(?:my|our)\s+" + _MOD + r"*(" + _RELATIVE_WORD + r")\b"
                        r"((?:['’]s\s+" + _MOD + r"*" + _RELATIVE_WORD + r"\b)*)", re.I)
# A chain that goes on to someone else is not the subject: "my mother's friend",
# "my mother-in-law", "my mother's side".
_CHAIN_GOES_ON = re.compile(r"(?:['’]s|s['’])\s|\s*-?\s*in-?laws?\b", re.I)
# A two-link chain through a parent: my mother's brother is my maternal uncle.
_THROUGH_PARENT = {"mother": "grandmother", "father": "grandfather", "parent": "grandparent",
                   "brother": "uncle", "sister": "aunt"}
_SIDE = {"mother": "maternal", "father": "paternal"}


# "my brother and sister", "my mom and dad": a relative joined to a chain is the
# client's too (round 3 L1).
_JOINED_RELATIVE = re.compile(r"\s*(?:,|\band\b|\bor\b|&)\s*(?:(?:my|our)\s+)?" + _MOD +
                              r"*(" + _RELATIVE_WORD + r")\b", re.I)


def _family_targets(quote):
    """{relative: side} for each "my ..." relative chain in the quote. A chain that
    cannot be named as one relative ("my mother's son") gives nothing."""
    out = {}
    for m in _REL_CHAIN.finditer(quote or ""):
        if not m.group(2):
            at = m.end()
            while True:
                j = _JOINED_RELATIVE.match(quote, at)
                if not j or _CHAIN_GOES_ON.match(quote, j.end()):
                    break
                out.setdefault(_RELATIVES[j.group(1).casefold()], None)
                at = j.end()
        if _CHAIN_GOES_ON.match(quote, m.end()):
            continue
        links = [_RELATIVES[m.group(1).casefold()]]
        links += [_RELATIVES[w.casefold()] for w in re.findall(_RELATIVE_WORD, m.group(2), re.I)]
        if len(links) == 1:
            out[links[0]] = None
        elif len(links) == 2 and links[0] in ("mother", "father", "parent") \
                and links[1] in _THROUGH_PARENT:
            out[_THROUGH_PARENT[links[1]]] = _SIDE.get(links[0])
    return out


def _own_family_fact(quote, relative):
    """The value names exactly one relative, the one the quote's "my ..." chain names,
    and a side ("Maternal uncle") only when the quote supports it. Only the allowed
    modifiers may sit inside the chain, so "my adoptive mother", "my step mother" and
    "my friends mother" name no relative at all."""
    targets = _family_targets(quote)
    words = _tokens(relative)
    named = {_RELATIVES[w] for w in words if w in _RELATIVES}
    if len(named) != 1:
        return False
    who = next(iter(named))
    if who not in targets:
        return False
    q = _tokens(quote)
    for side, parent in (("maternal", r"(?:mother|mom|mum)"), ("paternal", r"(?:father|dad)")):
        if side in words and targets[who] != side and side not in q and not re.search(
                r"\b" + parent + r"['’]?s?\s+side\b", quote, re.I):
            return False
    return True


# --- what is not a fact (round 1 M1, L5; round 2 quoted speech) ----------------
_HYPOTHETICAL = re.compile(r"\b(?:if|whether|suppose|supposing|unless|in case|imagine)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|never|nor)\b|\b(?:do|does|did|have|has|had|am|is|are|was|"
                       r"were|wo|would)n['’]?t\b|\bain['’]?t\b", re.I)
_SUSPECT = re.compile(r"\b(?:might|maybe|possibly|perhaps|probably|suspect|suspected|wonder|"
                      r"wondering|unsure|not sure|may have|may be|could be|could have|i think|"
                      r"i guess|i believe|i feel like|thinks?|suspects?|worr(?:y|ied|ies)|"
                      r"afraid|scared|fear|at (?:high )?risk|risk (?:of|for)|tested for|"
                      r"testing for|screened for|screening for|negative for|tested negative|"
                      r"came back negative|ruled out|rule out|checked for)\b", re.I)
_PLANNED = re.compile(r"\b(?:trying to|try to|going to|gonna|plan(?:ning|s)? to|scheduled|"
                      r"booked|next (?:week|month|year|time)|tomorrow|needs? (?:a|an|to)|"
                      r"wants? me to|told me to|asked me to|advised me to|recommended|"
                      r"should (?:start|take|get|have)|will (?:start|take|have|be|get)|i'll|"
                      r"i will|thinking (?:of|about)|considering|about to|hope to|hoping to|"
                      r"waiting for|waiting list|wait ?list|on the list for|due for|"
                      r"want(?:s|ed)? (?:a|an|to have|to start|to try|to take|"
                      r"to begin|to do)\b)", re.I)
# "I'm getting my gallbladder out", "I'm having surgery in May": a booked event. Read
# for surgeries, dental and vaccinations only, since "I'm having headaches" is now.
_PROGRESSIVE = re.compile(r"\b(?:i'm|im|i am)\s+(?:getting|having|going in for|going for|"
                          r"booked|due)\b", re.I)
# After the quote: doubt, a medicine not started, or a date to come (round 3 H2).
_TRAIL_NOT = re.compile(
    r"\b(?:or at least|thinks? so|suspects? so|not sure|not confirmed|unconfirmed|supposedly|"
    r"apparently|allegedly|possibly|maybe|probably|not yet|next (?:week|month|year)|tomorrow|"
    r"later this \w+)\b|\b(?:haven['’]?t|have not|never|didn['’]?t|did not)\s+"
    r"(?:started|taken|begun|filled|tried|done|had)\b", re.I)
# A wish to be pregnant is not a pregnancy (round 3 M1).
_PREG_WISH = re.compile(r"\b(?:want|wanting|like to|i'd like|would like|hope|hoping|wish|"
                        r"trying|try|planning|plan to|conceive|ttc)\b", re.I)
# A health goal is something the client wants for themself.
_GOAL = re.compile(r"\b(?:want|wanting|goal|goals|hope|hoping|like to|love to|trying to|"
                   r"try to|aim|aiming|wish|working on|get rid of|get over)\b", re.I)
_NOT_GOAL = re.compile(r"\bwant(?:s|ed)? to (?:know|ask|check|see|find out|understand|learn|"
                       r"confirm|make sure)\b", re.I)
_SPEECH = re.compile(r"(\S+)\s+(?:said|says|wrote|writes|told me|tells me|asked|asks)\b(.*)$")


def _before(quote, sentence):
    """The sentence's text before the quote, squashed."""
    flat, q = _squash(sentence), _squash(quote)
    at = flat.find(q)
    return flat[:at] if at > 0 else ""


def _reported(quote, client_msg, sentence, family):
    """True when the quote is someone else's words: inside quotation marks, or direct
    speech after another person's "said," or "told me:". On family history any "said"
    or "told me" by another person counts, because their "my mother" is theirs."""
    flat, q = _squash(client_msg), _squash(quote)
    if len(re.findall(r'["“”]', flat[:flat.find(q)])) % 2 == 1:
        return True
    m = _SPEECH.search(_before(quote, sentence))
    if not m or m.group(1) in ("i", "i've", "ive"):
        return False
    tail = m.group(2)
    if re.fullmatch(r"\s*[,:]\s*[\"“'‘]?\s*|\s*[\"“'‘]\s*", tail):
        return True                      # direct speech
    # Indirect speech by a relative keeps the client's "my": "My mom told me my
    # grandmother had diabetes" is how clients learn family history (round 3 L1).
    if m.group(1) in _RELATIVES:
        return False
    return family and bool(re.fullmatch(r"\s*(?:that\s*)?", tail))


# A conjoined fact inherits the client's subject: in "I have type 2 diabetes and high
# blood pressure" the model quotes "high blood pressure" (live run, 20 Oct 2026).
_JOINED_END = re.compile(r"(?:,|\band|\bplus|\bas well as|&)\s*(?:also\s+)?$|\balso\s*$", re.I)


def _term_said(term, clause):
    """The key term is in the clause as whole words, generic surgery words aside, or
    as one of its synonyms. Stricter than _term_in_quote: no near-spelling."""
    for alt in _TERM_SYNONYMS.get(" ".join(_tokens(term)), ()):
        if re.search(r"\b" + alt + r"\b", clause, re.I):
            return True
    toks = _tokens(clause)
    removed = bool(_SAYS_REMOVED.search(clause))
    need = [w for w in _significant(term)
            if w not in _ALWAYS_GENERIC and not (removed and w in _REMOVAL_WORDS)]
    # A term of generic words only is refused later by _term_in_quote.
    return all(w in toks for w in need)


def _clause_with_term(term, client_msg):
    """The one clause of the client's message that says the key term, or None when
    no clause or more than one does. Clauses are the sentences split as _clauses
    splits them."""
    hits = [c.strip(" .!?") for sent in re.split(r"(?<=[.!?])\s+|\n+", client_msg or "")
            for c in _clauses(sent) if _term_said(term, c)]
    return hits[0] if len(hits) == 1 else None


def _conjoined_lead(quote, sentence):
    """The client's own first-person statement the quote is joined to, or None. The
    quote must follow it straight after "and", "plus", "as well as", "also" or a
    comma, in the same clause, and that statement must name no one else."""
    before = _before(quote, sentence)
    m = _JOINED_END.search(before)
    if not before or not m:
        return None
    seg = re.split(r"[.;:!?]|\bbut\b|\bso\b", before[:m.start()])[-1]
    if not _FIRST_PERSON.search(seg) or _OTHER_PERSON.search(seg):
        return None
    return seg


def _after(quote, sentence):
    """The sentence's text after the quote, squashed."""
    flat, q = _squash(sentence), _squash(quote)
    at = flat.find(q)
    return flat[at + len(q):] if at >= 0 else ""


# The client's own, though the words name someone: their own childhood, their own
# pregnancy or breastfeeding, a baby aspirin, a children's dose, and a life event that
# dates their own symptom ("since my mom died").
_OWN = re.compile(
    r"\bas an? (?:child|kid|baby|toddler|teen|teenager)\b|"
    r"\bsince (?:i was an? )?(?:child|kid|baby)hood\b|"
    r"\b(?:pregnant with|expecting|nursing|breast-?feed(?:ing|s)?|breast feeding)\s+"
    r"(?:(?:my|our|a|an)\s+)?(?:\w+\s+)?(?:bab(?:y|ies)|child|kids?|sons?|daughters?|"
    r"twins|little one)\b|"
    r"\bbaby aspirin\b|(?<!my )(?<!our )\b(?:children|kids?)['’]s?\s+(?=\w)|"
    r"\b(?:since|after|when|because)\s+(?:my|our)\s+(?:\w+\s+){1,2}?"
    r"(?:died|passed(?:\s+away)?|was born|were born|left|moved out)\b", re.I)
# Free-text fields where a negative is not a real answer to append (round 3 M5).
# "I am not vaccinated", "I don't drink" and "I don't sleep well" are real answers.
_TEXT_NEGATION_DROPS = ("physical_trauma", "toxins")
# Free-text fields where "need to", "trying to" or "I'll" are part of the symptom
# ("I wake and need to pee"), so only doubt is checked there.
_TEXT_DOUBT_ONLY = ("other_symptoms", "sleep")


# "It runs in my family" is family history, never the client's own fact.
_FAMILY_TRAIT = re.compile(r"\bruns? in (?:my|our|the) family\b|\bfamily history\b", re.I)


def _normalize_item(item, client_msg):
    if not isinstance(item, dict):
        return None
    fid = item.get("field_id")
    if fid not in EXTRACT_FIELDS:
        return None
    family = fid == "family_history"
    table = fid in TABLE_COLUMNS
    if item.get("about") != ("family" if family else "self"):
        return None
    quote = _cell(item.get("quote"), 300)
    # The evidence must be the client's own words, so nothing from the AI's
    # answer, and nothing the model made up, can become a suggestion.
    if not quote:
        return None
    if _squash(quote) not in _squash(client_msg):
        # The model rewrote the quote ("I have high blood pressure" from "...diabetes
        # and high blood pressure", "I have me on levothyroxine"), live runs 20 Oct.
        # For a table field the client's own clause that says the key term becomes
        # the quote, and every check below runs on it. Free text has no key term.
        # A rewrite that names someone else is the model reading it as theirs.
        row = normalize_row(fid, item.get("value")) if table else None
        if not row or (not family and _OTHER_PERSON.search(quote)):
            return None
        quote = _clause_with_term(row[TABLE_COLUMNS[fid][0]], client_msg)
        if not quote:
            return None                  # no clause, or more than one, says it
    sentence = _source_sentence(quote, client_msg)
    joined = None
    if not (_FIRST_PERSON.search(quote) or (family and _REL_CHAIN.search(quote))):
        joined = _conjoined_lead(quote, sentence) if table and not family else None
        if joined is None:
            return None                  # "our mother" is the client's own family too
    # Two words are enough for a table fact whose term the quote must name anyway
    # ("I'm pregnant"), one for a fact joined to the client's own ("and asthma");
    # free-text fields keep three.
    if len(quote.split()) < (1 if joined is not None else 2 if table else 3):
        return None
    if _reported(quote, client_msg, sentence, family):
        return None                      # someone else's words
    # A quote naming another person is not the client's fact. A relative elsewhere in
    # the sentence does not matter ("I have high blood pressure like my dad", round 1
    # M2). Family history has its own check below.
    if not family and (_OTHER_PERSON.search(_OWN.sub(" ", quote)) or _FAMILY_TRAIT.search(quote)):
        return None
    if _asked(quote, sentence):
        return None                      # a question is not a fact
    lead = joined if joined is not None else (_clauses(_before(quote, sentence)) or [""])[-1]
    if _HYPOTHETICAL.search(lead) or _HYPOTHETICAL.match(quote.lstrip()):
        return None                      # "If I had diabetes I would cut sugar."
    scope = lead + " " + quote
    trail = _after(quote, sentence)
    booked = fid in ("surgeries", "dental", "vaccinations") and _PROGRESSIVE.search(scope)
    if table:
        if _NEGATION.search(scope):
            return None                  # "I don't have diabetes", "not allergic"
        if fid == "health_concerns":
            if _NOT_GOAL.search(scope) or not _GOAL.search(quote):
                return None              # a goal is something the client wants
        elif _SUSPECT.search(scope) or _PLANNED.search(scope) or booked \
                or _TRAIL_NOT.search(trail):
            return None                  # "I might have Lyme", "I need a hip replacement"
        value = normalize_row(fid, item.get("value"))
        primary = TABLE_COLUMNS[fid][0]
        if not value or not _term_in_quote(value[primary], quote):
            return None
        for col in list(value):
            if col in (primary, "current", "relative"):
                continue
            if col in ("age", "age_onset"):
                keep = _age_stated(value[col], client_msg)
            else:
                keep = _cell_supported(value[col], client_msg)
            if not keep:
                del value[col]           # a reason, brand, reaction or age never said
        if family and not _own_family_fact(quote, value.get("relative", "")):
            return None
        if fid in _CURRENT_ONLY_TABLES and _is_past(quote, value[primary], trail):
            return None                  # "I stopped taking warfarin last year"
        if fid == "allergies" and _ENDED.search(quote + " " + trail):
            return None                  # "I outgrew my peanut allergy" (round 3 M3)
        if fid == "diagnoses":
            past = _is_past(quote, value["diagnosis"])
            if _PREGNANCY.search(value["diagnosis"]) or _PREGNANCY.search(quote):
                if past or _PREG_WISH.search(scope):
                    return None          # a past or wished-for pregnancy is not current
                value["current"] = "Current"
            else:
                value["current"] = "Past" if past else "Current"
    else:
        if _SUSPECT.search(scope):
            return None                  # "I might have a cavity"
        if fid not in _TEXT_DOUBT_ONLY and (_PLANNED.search(scope) or booked):
            return None                  # "I'm going to try the keto diet"
        if fid in _TEXT_NEGATION_DROPS and _NEGATION.search(scope):
            return None                  # "I have never broken a bone"
        if fid in _CURRENT_ONLY_TEXT and _is_past(quote, trail=trail):
            return None                  # a symptom that is over is not a current symptom
        value = quote                    # free text: the client's words, not a paraphrase
    if not value:
        return None
    return {"field_id": fid, "value": value, "rationale": f'"{quote}"'}


def llm_extractor(complete):
    """An extractor for extract_from_turn backed by `complete(system, user) -> str`
    (the same injected-call pattern as portal_triage.classify). The model sees the
    CLIENT'S message only, never the assistant's answer. Strict JSON; garbage or an
    error -> []."""
    def _extract(client_msg, assistant_msg=None):
        text = (client_msg or "").strip()
        if not text:
            return []
        try:
            data = _strict_json(complete(EXTRACT_SYSTEM, text))
        except Exception as e:
            print(f"[health-suggestions] extractor call failed: {e!r}", flush=True)
            return []
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out, seen = [], set()
        for item in items:
            norm = _normalize_item(item, text)
            if not norm:
                continue
            key = (norm["field_id"], json.dumps(norm["value"], sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            out.append(norm)
        return out
    return _extract


# --- applying a confirmed suggestion ----------------------------------------
# intake_public.merge_answers REPLACES a whole field. A one-row suggestion must
# not wipe a client's table, and a note must not overwrite their own words, so
# the resolve path builds the merged value here first.

def _form_field(field_id):
    from dashboard import intake
    for f in intake._fields():
        if f["id"] == field_id:
            return f
    return None


def _row_key(row):
    return tuple(sorted((k, _squash(v)) for k, v in (row or {}).items()
                        if _squash(v)))


# A table whose form lists an optional column first. Supplements start with
# "brand", but a supplement named without a brand is still a row: "name" is the
# column a row needs, and the one plain text lands in.
_PRIMARY_COL = {"supplements": "name"}


def _table_cols(field_id):
    """The form's column ids, the column a row needs first."""
    f = _form_field(field_id)
    if not f or f.get("type") != "table":
        return None
    cols = [c["id"] for c in f.get("columns") or []]
    primary = _PRIMARY_COL.get(field_id)
    if primary in cols:
        cols.remove(primary)
        cols.insert(0, primary)
    return cols


def _row_from_dict(cols, value):
    row = {c: value[c] for c in cols
           if c in value and value[c] not in (None, "") and not isinstance(value[c], (dict, list))}
    if not row or not str(row.get(cols[0], "")).strip():
        return None
    return row


def table_rows_from(field_id, value):
    """Decode a confirmed or edited value for a table field into row dicts, using the
    form's own columns. Accepts a row dict, its JSON text, a list of rows, or plain
    text (an old clinician suggestion): plain text becomes a first-column row, never
    an error. Empty list when nothing usable."""
    cols = _table_cols(field_id)
    if not cols:
        return []
    if isinstance(value, str):
        text = value.strip()
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list)):
            value = decoded
        else:
            return [{cols[0]: text}] if text else []
    items = value if isinstance(value, list) else [value]
    rows = []
    for item in items:
        if isinstance(item, dict):
            row = _row_from_dict(cols, item)
        elif isinstance(item, str) and item.strip():
            row = {cols[0]: item.strip()}
        else:
            row = None
        if row:
            rows.append(row)
    return rows


def table_row_from(field_id, value):
    """The first usable row of table_rows_from, or None."""
    rows = table_rows_from(field_id, value)
    return rows[0] if rows else None


def _existing_rows(field_id, current):
    """A table's current value as rows, never losing anything. Text (a table that
    was saved as "Warfarin 5 mg, for atrial fibrillation") becomes ONE first-column
    row, never split at commas: a comma inside a reason is not a second row (round 3
    L4). String items become first-column rows."""
    cols = _table_cols(field_id) or ["value"]
    if current is None or current == "":
        return []
    if isinstance(current, str):
        text = current.strip()
        return [{cols[0]: text}] if text else []
    if isinstance(current, dict):
        return [current]
    if not isinstance(current, list):
        return [{cols[0]: str(current)}]
    out = []
    for item in current:
        if isinstance(item, dict):
            out.append(item)
        elif item is not None and str(item).strip():
            out.append({cols[0]: str(item).strip()})
    return out


def apply_suggestion(field_id, current, value):
    """The field's new value once a suggestion is confirmed or edited.
    - table: append each new row to the existing rows. Existing text or string items
      are kept as first-column rows. A row equal to an existing one (case and spacing
      ignored) is skipped.
    - text/textarea: append on a new line. Skip only when an existing LINE equals the
      new text (case and spacing ignored), never on a substring: "2 beers nightly"
      is not "12 beers nightly".
    - anything else (scales, numbers, choices): the value replaces, as before.
    Raises ValueError when a table value holds no usable row."""
    f = _form_field(field_id) or {}
    ftype = f.get("type")
    if ftype == "table":
        new_rows = table_rows_from(field_id, value)
        if not new_rows:
            raise ValueError("not a table row")
        rows = _existing_rows(field_id, current)
        for row in new_rows:
            # A row already held, or already held with more columns, adds nothing
            # ("Metformin" beside "Metformin, for diabetes"), round 3 L3.
            if not any(set(_row_key(row)) <= set(_row_key(r)) for r in rows):
                rows.append(row)
        return rows
    if ftype in ("text", "textarea"):
        new = str(value if value is not None else "").strip()
        if isinstance(current, (list, tuple)):
            # A list in a text field is its items, one per line, never Python's repr.
            current = "\n".join(str(x).strip() for x in current
                                if x is not None and str(x).strip())
        have = str(current if current is not None else "").strip()
        if not new:
            return have
        if not have:
            return new
        if any(_squash(line) == _squash(new) for line in have.split("\n")):
            return have
        return have + "\n" + new
    return value


def extract_from_turn(client_msg, assistant_msg, extractor=None):
    """Parse one chat turn into candidate editable health-record edits:
    [{"field_id", "value", "rationale"}, ...]. Pure and testable — pass
    `extractor(client_msg, assistant_msg) -> list[dict]` to inject a fake (or
    a real LLM call) for tests; the return is always filtered down to
    `health_profile.EDITABLE_FIELD_IDS` before it comes back, so an
    extractor can never smuggle in an identity/consent field.

    With no `extractor` given this returns []. The portal chat passes
    `llm_extractor(<Haiku call>)` from app.py (_extract_portal_health_suggestions),
    where the Anthropic client lives.
    """
    if extractor is None:
        candidates = []
    else:
        candidates = extractor(client_msg, assistant_msg) or []
    # Lazy import: health_profile imports dashboard.intake, and nothing here
    # is needed at module load time, so import inside the function to avoid
    # any import-cycle risk with modules that import health_suggestions.
    from dashboard import health_profile
    allowed = health_profile.EDITABLE_FIELD_IDS
    out = []
    for c in candidates:
        fid = (c or {}).get("field_id")
        if fid in allowed:
            out.append({"field_id": fid, "value": c.get("value"),
                        "rationale": c.get("rationale")})
    return out
