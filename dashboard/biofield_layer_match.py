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


def match_term(cx, kind, text, test_id=None):
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
    if is_balanced_cue(text):
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
    phrase, also_tail, _cue = _strip_cues(text)
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
