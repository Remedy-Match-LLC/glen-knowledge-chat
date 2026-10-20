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
# Only facts the client states about themself, in their own message, for these
# fields. The client confirms each one on the portal "Suggested updates" card.
# Placement: "pregnancy or breastfeeding can go in as current health conditions.
# Implants and pacemakers fit in surgeries. Heavy drinking and medical diet go in
# diet", with diet and alcohol as two separate fields.

# field_id -> its row columns, primary column first. A row needs the primary.
TABLE_COLUMNS = {
    "diagnoses": ("diagnosis", "current"),
    "surgeries": ("procedure", "reason", "age"),
    "medications": ("medication", "reason"),
    "otc_drugs": ("medication", "reason"),
    "allergies": ("sensitivity", "reaction"),
}
TEXT_FIELDS = ("diet", "alcohol")
EXTRACT_FIELDS = tuple(TABLE_COLUMNS) + TEXT_FIELDS

EXTRACT_SYSTEM = (
    "You read ONE message a client wrote in their health portal chat. Find only these facts "
    "that the client states about THEMSELF, as true now or in their past:\n"
    "- a CURRENT pregnancy or breastfeeding -> field \"diagnoses\", value {\"diagnosis\": "
    "\"Pregnancy\" or \"Breastfeeding\", \"current\": \"Current\"}. Never a past one.\n"
    "- epilepsy, a heart rhythm problem, kidney disease, gallstones, a blocked bile duct, or "
    "asthma -> field \"diagnoses\", value {\"diagnosis\": name, \"current\": \"Current\"}, "
    "or \"Past\" only when the client says it is in the past\n"
    "- a pacemaker or any other implant -> field \"surgeries\", value {\"procedure\": what was "
    "implanted, \"reason\": why if stated, \"age\": their age then if stated}\n"
    "- a regular prescription medicine (blood thinners such as warfarin, thyroid, diabetes, "
    "blood pressure, or any other) -> field \"medications\", value {\"medication\": name, "
    "\"reason\": what for if stated}\n"
    "- a regular non-prescription drug -> field \"otc_drugs\", value {\"medication\": name, "
    "\"reason\": what for if stated}\n"
    "- a food allergy -> field \"allergies\", value {\"sensitivity\": the food, "
    "\"reaction\": the reaction if stated}\n"
    "- a medical or special diet -> field \"diet\"; the quote is what gets recorded\n"
    "- heavy or regular drinking of alcohol -> field \"alcohol\"; the quote, with the amount, "
    "type and how often as stated, is what gets recorded\n"
    "Never record a fact about anyone else (a child, partner, friend, parent). Never guess or "
    "infer. Never record a question (\"is it safe if I were pregnant?\"). Leave out anything "
    "not on this list. For every item, \"quote\" is the client's exact words that state it "
    "(at least 3 words, including I or my, and the name you put in the value), copied from "
    "their message, and \"about\" is \"self\".\n"
    "Return STRICT JSON ONLY, no prose: {\"items\": [{\"field_id\": ..., \"value\": ..., "
    "\"quote\": ..., \"about\": \"self\"}]}. With nothing to record return {\"items\": []}."
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
        elif col == "age":
            try:
                cell = int(float(cell)) if cell else ""
            except ValueError:
                cell = ""
        if cell != "":
            row[col] = cell
    return row if row.get(cols[0]) else None


# Someone else. A quote, or the sentence it came from, naming one is not the
# client's own fact, whatever the model says in "about".
_OTHER_PERSON = re.compile(
    r"\b(?:wife|husband|daughters?|sons?|mom|mum|mother|dad|father|sisters?|brothers?|"
    r"friends?|partner|bab(?:y|ies)|child|children|kids?|boyfriend|girlfriend|"
    r"grand(?:ma|pa|mother|father|son|daughter)|aunt|uncle|cousin|niece|nephew|"
    r"neighbou?r|coworker|client|patient)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i've|ive|i'd|i'll|my|me|myself|mine)\b", re.I)
# Past tense in a quote. "I've had asthma since childhood" is present, so the
# present perfect is removed before the check.
_PRESENT_PERFECT = re.compile(r"\b(?:(?:i've|ive|i have|has|have)\s+(?:had|been)|"
                              r"(?:was|were|got|been) diagnosed)\b", re.I)
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


def _is_past(quote):
    return bool(_PAST.search(_PRESENT_PERFECT.sub(" ", quote)))


def _term_in_quote(term, quote):
    """Every word (3+ letters) of the key term appears in the quote, matched on its
    first 6 letters, so "Pregnancy" is supported by "pregnant"."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(term).casefold()) if len(w) >= 3]
    q = _squash(quote)
    return bool(words) and all(w[:6] in q for w in words)


def _normalize_item(item, client_msg):
    if not isinstance(item, dict):
        return None
    if item.get("about") != "self":
        return None
    fid = item.get("field_id")
    if fid not in EXTRACT_FIELDS:
        return None
    quote = _cell(item.get("quote"), 300)
    # The evidence must be the client's own words, so nothing from the AI's
    # answer, and nothing the model made up, can become a suggestion.
    if not quote or _squash(quote) not in _squash(client_msg):
        return None
    if len(quote.split()) < 3 or not _FIRST_PERSON.search(quote):
        return None
    sentence = _source_sentence(quote, client_msg)
    own = re.compile(r"\bas a (?:child|kid|baby)\b|\bsince (?:i was a )?(?:child|kid|baby)hood\b",
                     re.I)        # the client's own childhood, not another person
    if _OTHER_PERSON.search(own.sub(" ", quote)) or _OTHER_PERSON.search(own.sub(" ", sentence)):
        return None
    if sentence.rstrip().endswith("?"):
        return None                      # a question is not a fact
    if fid in TABLE_COLUMNS:
        value = normalize_row(fid, item.get("value"))
        if not value or not _term_in_quote(value[TABLE_COLUMNS[fid][0]], quote):
            return None
        if fid == "diagnoses":
            past = _is_past(quote)
            if _PREGNANCY.search(value["diagnosis"]) or _PREGNANCY.search(quote):
                if past:
                    return None          # a past pregnancy is not a current condition
                value["current"] = "Current"
            elif past:
                value["current"] = "Past"
    else:
        value = quote                    # diet and alcohol: the client's words, not a paraphrase
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


def _table_cols(field_id):
    f = _form_field(field_id)
    if not f or f.get("type") != "table":
        return None
    return [c["id"] for c in f.get("columns") or []]


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
    was saved as "MS, asthma") and string items become first-column rows."""
    cols = _table_cols(field_id) or ["value"]
    if current is None or current == "":
        return []
    if isinstance(current, str):
        parts = [p.strip() for p in re.split(r"[,;\n]", current) if p.strip()]
        return [{cols[0]: p} for p in parts]
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
            if not any(_row_key(r) == _row_key(row) for r in rows):
                rows.append(row)
        return rows
    if ftype in ("text", "textarea"):
        new = str(value if value is not None else "").strip()
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
