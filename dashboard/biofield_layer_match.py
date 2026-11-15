"""One spoken phrase matched to one layer field (live session part 2a, short mode).

Glen's rulings of 14 November 2026, platform/plans/2026-11-07-live-session-segments.md:

4. An unmatched phrase stays in the field, highlighted as unmatched. So match_term
   always returns the words, and says whether a known name matched.
5. "head and tail" said before or after the stress fills both Head and Tail
   (also_tail=True). A plain "head" fills the Head alone.
9. A dose spoken with a remedy fills dose and frequency. With none spoken, the
   catalogue default fills in, as today (merge_dosing, the same fill Interpret uses).
2. Saying "balanced" marks the layer. The phrase is read here, so the page has one
   place that decides it: a phrase that is only the cue word comes back balanced=True.
"""
import re
import sqlite3

from dashboard.biofield_authoring import (
    merge_dosing, remedy_dosing, resolve_remedy_match, resolve_stress_match, stress_vocab)

KINDS = ("head", "tail", "remedy")

_PUNCT = re.compile(r"[\s,.;:!?\-]+")
_HEAD_AND_TAIL = r"head\s+(?:and|&|n)\s+tail"
_HT_BEFORE = re.compile(r"^\s*" + _HEAD_AND_TAIL + r"\b[\s,.;:\-]*(?:is\s+)?", re.I)
_HT_AFTER = re.compile(r"[\s,.;:\-]*\b(?:is\s+)?(?:the\s+)?" + _HEAD_AND_TAIL
                       + r"\s*[.!?]*\s*$", re.I)
_CUE_BEFORE = re.compile(r"^\s*(head|tail)\b[\s,.;:\-]*(?:is\s+)?", re.I)
_CUE_AFTER = re.compile(r"[\s,.;:\-]*\b(?:is\s+)?(?:the\s+)?(head|tail)\s*[.!?]*\s*$", re.I)

# "balanced", "layer balanced", "this layer is balanced", "it's balanced."
_BALANCED = re.compile(
    r"^(?:(?:this|the|that)\s+)?(?:layer\s+)?(?:is\s+|it'?s\s+|its\s+)?balanced"
    r"(?:\s+layer)?$", re.I)

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "a": 1, "an": 1,
}
_UNITS = (r"drops?|capsules?|caps?|tablets?|tabs?|softgels?|lozenges?|pellets?|sprays?|"
          r"pumps?|squirts?|scoops?|teaspoons?|tsp|tablespoons?|tbsp|droppers?|"
          r"dropperfuls?|ml|millilit(?:er|re)s?|grams?|g|packets?|sachets?")
_WORDS = "|".join(k for k in _NUM_WORDS if k not in ("a", "an"))
# Longest forms first: a mixed number ("1 1/2", "one and a half") must not be read as
# its last part, which left "1" in the remedy name (review, 14 November 2026).
_QTY = (r"(?:\d+\s+\d+/\d+|(?:\d+|" + _WORDS + r")\s+and\s+a\s+half|\d+/\d+|"
        r"\d+(?:\.\d+)?|a\s+half|half|" + _WORDS + r"|an?)")
_NUM_TOKEN = re.compile(r"(?:\d+(?:[./]\d+)?|" + _WORDS + r")$", re.I)
_DOSE = re.compile(r"\b(" + _QTY + r")\s+(?:level\s+)?(" + _UNITS + r")\b\.?", re.I)
_FREQ = re.compile(
    r"\b(?:(?:once|twice|thrice|(?:\d+|one|two|three|four|five|six)\s+times?)\s+"
    r"(?:a|per|each)\s+(?:day|week)|(?:once|twice|thrice|(?:\d+|two|three|four)\s+times?)\s+"
    r"daily|(?:once|twice)\s+weekly|daily|every\s+(?:day|morning|evening|night|"
    r"\d+\s+hours?)|at\s+bedtime|morning\s+and\s+(?:evening|night)|as\s+needed)\b",
    re.I)


def is_balanced_cue(text):
    """True when the phrase is only the cue word "balanced" (ruling 2)."""
    core = " ".join(_PUNCT.sub(" ", str(text or "")).split())
    return bool(core) and bool(_BALANCED.match(core))


def _qty_text(q):
    q = " ".join(q.lower().split())
    if q in ("half", "a half"):
        return "1/2"
    m = re.match(r"^(\S+) and a half$", q)
    if m:
        whole = m.group(1)
        return f"{_NUM_WORDS.get(whole, whole)} 1/2"
    if q in _NUM_WORDS:
        return str(_NUM_WORDS[q])
    return q


def parse_dose(text):
    """(rest, dose, frequency, unsure) from a spoken remedy phrase. dose and frequency
    are '' when not spoken. "Liver Detox 10 drops twice a day" ->
    ("Liver Detox", "10 drops", "twice a day", False).

    unsure is True when another number sits right before the quantity ("2 3 drops"):
    the dose is then left blank. The whole run of numbers and the unit still come out
    of the name, so the remedy itself can be matched."""
    s = str(text or "")
    dose = freq = ""
    unsure = False
    m = _DOSE.search(s)
    if m:
        before = s[:m.start()].rstrip(" ,")
        words = before.split()
        if words and _NUM_TOKEN.search(words[-1]):
            unsure = True
            while words and _NUM_TOKEN.search(words[-1]):
                words.pop()
            s = " ".join(words) + " " + s[m.end():]
        else:
            dose = f"{_qty_text(m.group(1))} {m.group(2).lower()}"
            s = s[:m.start()] + " " + s[m.end():]
    f = _FREQ.search(s)
    if f:
        freq = " ".join(f.group(0).lower().split())
        s = s[:f.start()] + " " + s[f.end():]
    rest = " ".join(s.split()).strip(" ,.;:-")
    # Connector words left at either end once the dose is out: "of Sobopla", "Sobopla at".
    rest = re.sub(r"^(?:of|take|taking)\s+|\s+(?:at|of|with|take|taking)$", "", rest,
                  flags=re.I).strip(" ,.;:-")
    return rest, dose, freq, unsure


def _strip_cues(text):
    """(phrase, also_tail, cue) with any spoken field cue taken off either end.
    cue is 'head', 'tail' or None. "head and tail" sets also_tail."""
    s = str(text or "").strip()
    also_tail = False
    cue = None
    for rx in (_HT_BEFORE, _HT_AFTER):
        n = rx.sub(" ", s, count=1)
        if n != s:
            s, also_tail, cue = n, True, "head"
            break
    if not also_tail:
        for rx in (_CUE_BEFORE, _CUE_AFTER):
            m = rx.search(s)
            if m:
                cue = m.group(1).lower()
                s = s[:m.start()] + " " + s[m.end():]
                break
    return " ".join(s.split()).strip(" ,.;:-"), also_tail, cue


def detected_stress_labels(cx, test_id):
    """This client's detected stresses for a test, in the stress panel's order
    (required first, then by id). Operational CRM tags are left out, as the panel
    leaves them out. [] when the table is missing."""
    if test_id in (None, ""):
        return []
    try:
        from dashboard.biofield_stress import _clean_label, _is_operational_tag, _num
        rows = cx.execute(
            "SELECT code, label, source FROM biofield_auth_stress WHERE test_id=? "
            "ORDER BY CASE balance WHEN 'required' THEN 0 ELSE 1 END, id",
            (_num(test_id),)).fetchall()
    except sqlite3.Error:
        return []
    out, seen = [], set()
    for code, label, source in rows:
        if source == "tag" and _is_operational_tag(code or label):
            continue
        lbl = (_clean_label(label, source) or "").strip()
        if lbl and lbl.lower() not in seen:
            seen.add(lbl.lower())
            out.append(lbl)
    return out


def client_first_vocab(cx, test_id, q="", limit=5000):
    """Ruling 6, "client first": this client's detected stresses, then the full stress
    vocabulary, without repeats. stress_vocab is unchanged for every other caller."""
    client = detected_stress_labels(cx, test_id)
    if q:
        client = [c for c in client if q.strip().lower() in c.lower()]
    seen = {c.lower() for c in client}
    rest = [v for v in stress_vocab(cx, q, limit) if (v or "").lower() not in seen]
    return (client + rest)[:limit], client


def match_term(cx, kind, text, test_id=None, cues=True):
    """One spoken phrase -> one field value.

    Returns {"value", "matched"} always, plus:
      head/tail: "also_tail" True when "head and tail" was said before or after.
      remedy:    "dose" and "frequency" (spoken, else ''), and "dosing", the three
                 fields to fill: spoken values first, then the catalogue default.
                 "dose_unsure" True when a number sat next to the quantity: the dose
                 stays blank and matched is False, so the row waits for review.
      any kind:  "balanced": True when the phrase is only the cue word "balanced";
                 the page ticks the card's Balanced box and fills nothing.
    matched is False when nothing scored above the resolver's cutoff; value is then
    the spoken words, Title Cased, for Glen to correct from the drop-down."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = " ".join(str(text or "").split())
    # cues=False: the hands-free segmenter has already taken the cue words off, so the
    # words are matched exactly as they came ("Pressure in the Head" keeps its Head).
    if cues and is_balanced_cue(text):
        return {"value": "", "matched": True, "balanced": True}
    if kind == "remedy":
        rest, dose, freq, unsure = parse_dose(text)
        value, matched = resolve_remedy_match(cx, rest)
        # A dose it could not read for certain holds the row for review.
        out = {"value": value, "matched": bool(matched and value and not unsure),
               "dose": dose, "frequency": freq, "dose_unsure": unsure}
        defaults = remedy_dosing(cx, value) if value else {}
        out["dosing"] = merge_dosing(dose, freq, "", defaults)
        if unsure:
            out["dosing"]["dosage"] = ""       # never the catalogue's in place of a misheard one
        return out
    phrase, also_tail, _cue = _strip_cues(text) if cues else (text, False, None)
    extra = detected_stress_labels(cx, test_id)
    try:
        from dashboard.biofield_stress import _table_exists
        if _table_exists(cx, "custom_stress_vocab"):
            extra += [r[0] for r in cx.execute(
                "SELECT term FROM custom_stress_vocab "
                "WHERE TRIM(COALESCE(term,''))<>''").fetchall()]
    except sqlite3.Error:
        pass
    if _cue and not also_tail:
        # A plain "head" or "tail" may be part of a real stress ("Pressure in the
        # Head"), so the words as spoken win when they match a known name.
        raw, raw_ok = resolve_stress_match(cx, text, extra_names=extra)
        if raw_ok:
            return {"value": raw, "matched": True, "also_tail": False}
    value, matched = resolve_stress_match(cx, phrase, extra_names=extra)
    return {"value": value, "matched": bool(matched and value), "also_tail": also_tail}


# ---- hands-free recording (live session part 2b) ----------------------------------
#
# Glen, 14 November 2026: "yes" to the cue words. In the hands-free recording "head",
# "tail" and "remedy" each start that field, "head and tail" fills both, "balanced"
# ticks the layer and "next layer" starts a new layer. Ruling 9: a dose spoken after a
# remedy fills its dose and frequency.
#
# THE RULE, exactly:
#  1. An utterance is split into sentences at . ! ? ; or : followed by a space or the
#     end. A dot inside a number ("2.5 ml") or a colon in a time ("8:30") does not split.
#  2. A cue word counts only at a cue position: the start of a sentence, or straight
#     after another cue ("Next layer, head liver"). Anywhere else it is just a word, so
#     "Pressure in the Head" and "the head and tail of it" never split. Filler words at
#     a cue position are skipped first: okay, ok, alright, so, now, and, um, uh. So
#     "Okay, head liver" splits.
#  3. Cues are whole words, case-insensitive. At a cue position, "next layer" is read
#     first, then "head and tail" (also "head & tail", "head n tail"), then "head",
#     "tail" or "remedy". "Headache" and "remedies" are not cues.
#  4. The words after a field cue, to the end of the sentence, are that field's text.
#     A leading "is" is dropped ("Head is liver"). A cue with nothing after it ("Head.")
#     leaves the field open: the next sentence, in this utterance or the next, fills it.
#  5. "balanced" counts only as a whole sentence: "balanced", "layer balanced", "this
#     layer is balanced" (is_balanced_cue). "Unbalanced hormones" or "balanced
#     hormones" is not a cue.
#  6. A sentence with no cue at its start, after a remedy, that is only a dose ("Two
#     drops twice a day.") is that remedy's dose. In the same utterance it joins the
#     remedy's text; in a later one it comes out as a dose event for the last remedy.
#     Dose sentences keep attaching to that remedy ("Two drops. Twice a day."), across
#     utterances too, until a cue or a sentence that is not a dose.
#  7. A sentence with no cue that ends in "head and tail" fills both (ruling 5: either
#     order), when no field is open.
#  8. Any other sentence goes to the full transcript only. Nothing is written from it,
#     and it ends the last remedy's dose (rule 6).
#
# Glen, 14 November 2026, "yes" to the terrain cue words too. "terrain", "location" and
# "BSI" follow rules 2 to 4 (a cue position, whole words, the rest of the sentence is the
# text, a bare cue leaves it open for the next sentence). They fill the terrain panel,
# never a layer, so the current layer is left alone wherever they are said.
#  9. "terrain" + a phase: 1 to 5 as a digit or a word ("three"), or a phase name,
#     clinical or consumer ("Cleanse", "Terrain Refresh"). "phase", "is" and "number"
#     are skipped. A number and a name that disagree, or anything else, is not
#     understood: the value is None and nothing is saved (parse_phase).
# 10. "location" + words: the words, matched on the server against the stress list the
#     layer cards use. Unmatched words are kept, highlighted as unmatched (ruling 4).
# 11. "BSI" + exactly four numbers, each 1 to 21, as digits or words ("twenty one"),
#     separated by "by", "x", "times", "to", "and" or commas: "BSI 12 by 8 to 9 by 6"
#     fills A, B, C and D. Anything else is not understood: the value is None, nothing
#     is saved, and the page says so (parse_bsi). "B.S.I." and "b s i" count as "BSI".
# 12. A "balanced" sentence is read before an open terrain field (rule 5 comes first),
#     so "Terrain. Balanced." ticks the layer.
#
# Glen, 14 November 2026 (Bridge): "yes" to "also balances" + a stress name.
# 13. "also balances" (also "it also balances", "also balance") at a cue position adds
#     the stress to the current layer's balanced stresses, as the card's "Add stress"
#     box does. The words are matched on the server against the stress list; the page
#     adds only a matched name. It is a different cue from "balanced" (rule 5): that
#     must be the whole sentence, and "also balances X" never is.

_HF_SENTENCE = re.compile(r"[.!?;:]+(?=\s|$)")
_HF_NEXT = re.compile(r"next\s+layer\b", re.I)
_HF_BOTH = re.compile(_HEAD_AND_TAIL + r"\b", re.I)
_HF_FIELD = re.compile(r"(head|tail|remedy)\b", re.I)
_HF_FILLER = re.compile(r"^(?:(?:okay|ok|alright|so|now|and|um|uh)\b[\s,\-]*)*", re.I)
_HF_SEP = re.compile(r"^[\s,\-]*(?:is\s+)?", re.I)
_HF_TRAIL_BOTH = re.compile(r"^(.*?\S)[\s,\-]+(?:is\s+)?(?:the\s+)?" + _HEAD_AND_TAIL + r"$",
                            re.I)
_HF_TERRAIN = re.compile(r"(terrain|location|bsi)\b", re.I)
_HF_ALSO = re.compile(r"(?:it\s+)?also\s+balances?\b", re.I)
# "B.S.I." would otherwise end a sentence at its last dot, and "b s i" is three words.
_HF_BSI_SPELLED = re.compile(r"\bb\.?\s?s\.?\s?i\b\.?", re.I)
TERRAIN_CUES = ("terrain", "location", "bsi")
PENDING = ("head", "tail", "remedy", "head_tail", "also_balances") + TERRAIN_CUES

_UNITS_1_19 = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30}
_BSI_SEPS = {"by", "x", "times", "to", "and"}
_PHASE_SKIP = {"terrain", "phase", "is", "number", "the", "a", "dot", "it's", "its"}


def _phase_stems():
    from dashboard.terrain_phase import PHASE_CLINICAL_NAMES, PHASE_NAMES
    stems = {}
    for n in PHASE_NAMES:
        for name in (PHASE_CLINICAL_NAMES[n], PHASE_NAMES[n].split()[-1]):
            # A five-letter stem, so "revived", "energise" and "relieve" still count.
            stems[name.lower()[:5]] = n
    return stems


def _words(text):
    return [w for w in re.split(r"[\s,.;:!?\-]+", str(text or "").lower()) if w]


def parse_phase(text):
    """The terrain phase 1-5 from the words after "terrain", else None (rule 9)."""
    stems = _phase_stems()
    found = set()
    for w in _words(text):
        if w in _PHASE_SKIP:
            continue
        if w.isdigit():
            n = int(w)
        elif w in _UNITS_1_19:
            n = _UNITS_1_19[w]
        else:
            n = stems.get(w[:5]) if len(w) >= 5 else None
        if n is None or not 1 <= n <= 5:
            return None
        found.add(n)
    return found.pop() if len(found) == 1 else None


def parse_bsi(text):
    """([A, B, C, D], '') from the words after "BSI", or (None, why) (rule 11)."""
    from dashboard.terrain_phase import bsi_num
    s = str(text or "").lower().replace("\u00d7", " x ")
    s = re.sub(r"(\d)\s*x\s*(?=\d)", r"\1 x ", s)          # "12x8"
    s = re.sub(r"^(?:is|reads?|of)\s+", "", s.strip())
    nums, words = [], _words(s)
    i = 0
    while i < len(words):
        w = words[i]
        if w in _BSI_SEPS:
            i += 1
            continue
        if w.isdigit():
            nums.append(int(w))
        elif w in _TENS:
            n = _TENS[w]
            if i + 1 < len(words) and words[i + 1] in _UNITS_1_19 \
                    and 1 <= _UNITS_1_19[words[i + 1]] <= 9:
                n += _UNITS_1_19[words[i + 1]]
                i += 1
            nums.append(n)
        elif w in _UNITS_1_19:
            nums.append(_UNITS_1_19[w])
        else:
            return None, f"'{w}' is not a number"
        i += 1
    if len(nums) != 4:
        return None, f"heard {len(nums)} number{'' if len(nums) == 1 else 's'}, not 4"
    bad = [n for n in nums if bsi_num(n) is None]
    if bad:
        return None, f"{bad[0]} is outside 1 to 21"
    return nums, ""


def _terrain_event(kind, words):
    """One terrain cue's event. Pure: the location is matched in handsfree_events."""
    from dashboard.terrain_phase import PHASE_CLINICAL_NAMES, bsi_warnings
    ev = {"terrain": kind, "text": words}
    if kind == "terrain":
        ev["terrain"] = "phase"
        n = parse_phase(words)
        ev["value"] = n
        ev["name"] = PHASE_CLINICAL_NAMES.get(n, "")
    elif kind == "bsi":
        nums, why = parse_bsi(words)
        ev["value"] = nums
        ev["error"] = why
        ev["warnings"] = bsi_warnings(*nums) if nums else []
    return ev


def _hf_sentences(text):
    out = []
    text = _HF_BSI_SPELLED.sub("BSI", " ".join(str(text or "").split()))
    for part in _HF_SENTENCE.split(text):
        part = part.strip(" ,-")
        if part:
            out.append(part)
    return out


def _hf_rest(s, m):
    rest = s[m.end():]
    return rest[_HF_SEP.match(rest).end():].strip(" ,-")


def _dose_only(s):
    """(dose, frequency) when the sentence is a dose and nothing else, else None."""
    rest, dose, freq, unsure = parse_dose(s)
    if rest or unsure or not (dose or freq):
        return None
    return dose, freq


def segment_utterance(text, pending=None, after_remedy=False):
    """One finalized utterance of the hands-free recording -> events, in order.

    Events: {"field": "head"|"tail"|"remedy", "text", "both"} ("both" True for "head
    and tail", which fills Head and Tail), {"field": "dose", "text", "dose",
    "frequency"} for the last remedy, {"balanced": True}, {"next_layer": True}, and
    the terrain panel's {"terrain": "phase"|"location"|"bsi", "text", ...} (rules 9-11:
    "value" is the phase or the four BSI numbers, None when not understood), and
    {"also_balances": True, "text"} for a stress the current layer also balances.

    pending is the field left open by a cue said with nothing after it (one of
    PENDING, or None). after_remedy is True when the last event was a remedy, so a
    dose said next belongs to it. Both carry over to the next utterance: they are
    returned, and the page sends them back. Pure: no database, no side effects."""
    pending = pending if pending in PENDING else None
    after_remedy = bool(after_remedy)
    events = []

    def field(kind, words, both=False):
        events.append({"field": kind, "text": words, "both": both})

    for sent in _hf_sentences(text):
        s = sent
        while s:
            s = s[_HF_FILLER.match(s).end():].strip(" ,-")
            if not s:
                break
            if is_balanced_cue(s):
                events.append({"balanced": True})
                pending, after_remedy = None, False
                break
            m = _HF_NEXT.match(s)
            if m:
                events.append({"next_layer": True})
                pending, after_remedy = None, False
                s = _hf_rest(s, m)
                continue                  # straight after a cue is a cue position
            m = _HF_ALSO.match(s)
            if m:
                rest = _hf_rest(s, m)
                if rest:
                    events.append({"also_balances": True, "text": rest})
                    pending = None
                else:
                    pending = "also_balances"
                after_remedy = False
                break
            m = _HF_TERRAIN.match(s)
            if m:
                cue = m.group(1).lower()
                rest = _hf_rest(s, m)
                if rest:
                    events.append(_terrain_event(cue, rest))
                    pending = None
                else:
                    pending = cue
                after_remedy = False      # a cue ends the remedy's dose (rule 6)
                break
            m = _HF_BOTH.match(s)
            kind, both = ("head", True) if m else (None, False)
            if not m:
                m = _HF_FIELD.match(s)
                kind = m.group(1).lower() if m else None
            if kind:
                rest = _hf_rest(s, m)
                if rest:
                    field(kind, rest, both)
                    pending = None
                else:
                    pending = "head_tail" if both else kind
                after_remedy = kind == "remedy" and bool(rest)
                break
            # No cue at the start of this sentence.
            if pending == "also_balances":
                events.append({"also_balances": True, "text": s})
                pending = None
                break
            if pending in TERRAIN_CUES:
                events.append(_terrain_event(pending, s))
                pending = None
                break
            if pending:
                kind = "head" if pending == "head_tail" else pending
                field(kind, s, pending == "head_tail")
                after_remedy = kind == "remedy"
                pending = None
                break
            dz = _dose_only(s) if after_remedy else None
            if dz:
                last = events[-1] if events else None
                if last and last.get("field") == "remedy":
                    last["text"] = f"{last['text']} {s}"
                elif last and last.get("field") == "dose":
                    last["text"] = f"{last['text']} {s}"
                    both_dz = _dose_only(last["text"])
                    if both_dz:
                        last["dose"], last["frequency"] = both_dz
                    else:
                        last["dose"] = last["dose"] or dz[0]
                        last["frequency"] = last["frequency"] or dz[1]
                else:
                    events.append({"field": "dose", "text": s, "dose": dz[0],
                                   "frequency": dz[1]})
                break                     # after_remedy stays on: more dose may follow
            after_remedy = False          # plain talk ends the remedy's dose
            t = _HF_TRAIL_BOTH.match(s)
            if t:
                field("head", t.group(1).strip(" ,-"), True)
                after_remedy = False
            break
    return {"events": events, "pending": pending, "after_remedy": after_remedy}


def handsfree_events(cx, text, pending=None, after_remedy=False, test_id=None):
    """segment_utterance, with each Head, Tail or Remedy event matched the way a short
    recording is (match_term, the cue words already off). Writes nothing: the page
    applies the events, in order, through the row routes, as hands-free rows."""
    out = segment_utterance(text, pending, after_remedy)
    for ev in out["events"]:
        kind = ev.get("field")
        if kind in KINDS:
            ev["match"] = match_term(cx, kind, ev["text"], test_id=test_id, cues=False)
        elif ev.get("terrain") == "location" or ev.get("also_balances"):
            # Rule 10: the stress list the layer cards use, this client's first.
            ev["match"] = match_term(cx, "head", ev["text"], test_id=test_id, cues=False)
    return out
