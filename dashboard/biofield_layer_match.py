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


# Glen, 15 Nov 2026: the Location drop-down opens with three sections, "SPIRIT ...
# stresses added in the live session, MIND ... the Clinical stress patterns, BODY ...
# the purple findings from the E4L scan", then the full list. Spirit is the stress
# panel's own Spirit group (stress_group in biofield_report_html): 'voice' (said in the
# session, or captured from its transcript) and 'manual' (added by hand on the page).
# Glen, 15 Nov 2026: the stresses written from a layer the session built ('chain')
# "move to body", as Body's first group. Each layer's head joins Spirit instead.
SPIRIT_STRESS_SOURCES = ("voice", "manual")
CHAIN_STRESS_SOURCE = "chain"
# BODY's subheads, in this order (Glen, 15 Nov 2026). "Causal chain" is our name for
# the chain group; the other three are his.
BODY_GROUPS = ("Causal chain", "Infoceuticals", "Body stresses", "Psyche")


def _dedupe(labels):
    out, seen = [], set()
    for label in labels or ():
        lbl = " ".join(str(label or "").split())
        if lbl and lbl.lower() not in seen:
            seen.add(lbl.lower())
            out.append(lbl)
    return out


_DIGITS = re.compile(r"(\d+)")


def natural_key(label):
    """Glen, 15 Nov 2026: "each sub-list is alphabetical, but numbers are in numerical
    sequence not one digit at a time, e.g. ED4 before ED11." Case-insensitive, and each
    run of digits compares as a number. re.split always starts with text, so every
    position holds the same type in every key and the tuples always compare."""
    parts = _DIGITS.split(" ".join(str(label or "").split()).casefold())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def natural_sorted(labels):
    """Stable: labels with the same key keep their order."""
    return sorted(labels, key=natural_key)


# Glen, 15 Nov 2026: "For infoceuticals, use the full name starting with the code, e.g.
# ED1: Source". Which codes are infoceuticals is biofield_clinical_sections' rule (ED,
# EI, ES, ET, MB and BFA; not MR or ER). The name comes from e4l.db's e4l_items.
def _info_match(label):
    from dashboard.biofield_clinical_sections import _INFOCEUTICAL_CODE
    return _INFOCEUTICAL_CODE.match(label)


def infoceutical_codes(labels):
    """The infoceutical codes the labels start with, upper case."""
    out = set()
    for label in labels or ():
        m = _info_match(" ".join(str(label or "").split()))
        if m:
            out.add(m.group(1).upper())
    return out


def stress_display(label, code_names=None):
    """"ED1", "ED1 Source" and "ED1: Source" -> "ED1: Source". The words after the code
    are kept when there are any ("ED10 Skin Epigenetics" -> "ED10: Skin Epigenetics");
    a bare code takes its name from `code_names` ({CODE: name}), and stays a bare code
    when no name is known. Anything else is returned with its spaces tidied."""
    lbl = " ".join(str(label or "").split())
    m = _info_match(lbl)
    if not m:
        return lbl
    code = m.group(1).upper()
    rest = lbl[m.end(1):].lstrip(" :.-").strip()
    name = rest or ((code_names or {}).get(code) or "").strip()
    return f"{code}: {name}" if name else code


def stress_key(label):
    """The dedupe key: case, spaces and punctuation ignored ("ED1: Source" = "ed1 source")."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(label or "").casefold()).split())


def _display_list(labels, code_names, fold_names):
    """Each label in its display form, each once. With `fold_names`, a plain name that
    is the name of an infoceutical in the same list ("Source" beside "ED1: Source") is
    the same item and is dropped."""
    shown = [stress_display(l, code_names) for l in labels or ()]
    info_names = set()
    if fold_names:
        info_names = {stress_key(d.split(":", 1)[1]) for d in shown
                      if ":" in d and _info_match(d)}
    out, seen = [], set()
    for d in shown:
        k = stress_key(d)
        if not k or k in seen or (not _info_match(d) and k in info_names):
            continue
        seen.add(k)
        out.append(d)
    return out


def _stress_labels(cx, test_id, sources):
    if test_id in (None, ""):
        return []
    try:
        from dashboard.biofield_stress import _num
        marks = ",".join("?" for _ in sources)
        rows = cx.execute(
            f"SELECT label FROM biofield_auth_stress WHERE test_id=? AND source IN ({marks}) "
            "ORDER BY id", (_num(test_id), *sources)).fetchall()
    except sqlite3.Error:
        return []
    return _dedupe(r[0] for r in rows)


def spirit_stress_labels(cx, test_id):
    """This test's live-session stresses, oldest first (sectioned_vocab sorts them).
    [] when the table is missing."""
    return _stress_labels(cx, test_id, SPIRIT_STRESS_SOURCES)


def chain_stress_labels(cx, test_id):
    """This test's stresses written from the causal chain, oldest first. BODY's first
    group. [] when the table is missing."""
    return _stress_labels(cx, test_id, (CHAIN_STRESS_SOURCE,))


def layer_heads(cx, test_id):
    """Each layer's Head text for this test, in layer order, each once. Glen, 15 Nov
    2026: a head ("Liver Detox") shows as a stress in SPIRIT."""
    if test_id in (None, ""):
        return []
    try:
        from dashboard.biofield_stress import _num
        rows = cx.execute("SELECT head FROM biofield_auth_chain WHERE test_id=? "
                          "ORDER BY layer, sort_seq, id", (_num(test_id),)).fetchall()
    except sqlite3.Error:
        return []
    return _dedupe(r[0] for r in rows)


def on_layer_labels(cx, test_id):
    """The stresses already on a layer: linked to a layer row that still exists
    (biofield_auth_layer_stress, as list_stresses reads it), plus each layer's head.
    Glen, 15 Nov 2026: "Grayed out means they're already balanced on a layer". The
    layer's Balanced tick is not consulted: on test 67 it is off while its five chain
    stresses sit on the layer. most_affected is never split on commas, since a label
    can hold one ("Shoulder, Arm & Emotional Integration")."""
    if test_id in (None, ""):
        return []
    linked = []
    try:
        from dashboard.biofield_stress import _num
        rows = cx.execute(
            "SELECT s.label, s.code FROM biofield_auth_layer_stress ls "
            "JOIN biofield_auth_stress s ON s.id=ls.stress_id AND s.test_id=ls.test_id "
            "JOIN biofield_auth_chain c ON c.id=ls.chain_rid AND c.test_id=ls.test_id "
            "WHERE ls.test_id=? ORDER BY s.id", (_num(test_id),)).fetchall()
    except sqlite3.Error:
        rows = []
    for label, code in rows:
        linked.append(label)
        # A scan stress is stored by its E4L name ("Nerve Driver") with its code
        # ("ED4"); the drop-down shows that infoceutical as "ED4: Nerve", so the code
        # is on the layer too.
        m = _info_match(" ".join(str(code or "").split()))
        if m:
            linked.append(m.group(1).upper())
    return _dedupe(linked + layer_heads(cx, test_id))


def sectioned_vocab(cx, test_id, mind=(), body=(), limit=5000, code_names=None,
                    psyche=()):
    """{"spirit", "mind", "body", "body_groups", "on_layer", "all", "unnamed"} for the
    sectioned stress drop-down.

    `mind` (the Clinical Summary's stress patterns) and `body` (the scan's purple
    findings) are read by the caller, which holds the profile and the scan.
    `code_names` is {CODE: name} for the infoceuticals (e4l_scan_marks.e4l_code_names).
    Each list is in display form ("ED1: Source"), deduped within itself, and in natural
    order (natural_key); the page filters and never reorders. `all` is the full list
    the plain drop-down held, so an item may sit in a section and in `all` too. In the
    sections a plain name folds into its infoceutical ("Source" into "ED1: Source"); in
    `all` it does not, since "Heart" there is a stress of its own beside "ED6: Heart".
    `unnamed` lists the infoceutical codes shown bare because no name is known.

    Glen, 15 Nov 2026: BODY is the causal chain's stresses, then the purple bars, in
    the groups of BODY_GROUPS. `body_groups` is [[subhead, [labels]]] for the groups
    with anything in them; `body` is the same labels in one list, in that order. A
    purple bar that is not an infoceutical is Psyche when it is in `psyche` (the bars
    on the scan's psyche pages), else Body stresses. SPIRIT is the live session's own
    stresses plus each layer's head, a head left out when it is already in BODY or
    MIND. `on_layer` lists the stresses already on a layer (on_layer_labels), which the
    page shows greyed in every section."""
    vocab, _client = client_first_vocab(cx, test_id, "", limit)
    chain = list(chain_stress_labels(cx, test_id))
    out = {"mind": list(mind or ()), "body": chain + list(body or ()), "all": list(vocab)}
    for key in out:
        out[key] = natural_sorted(_display_list(out[key], code_names, key != "all"))
    def keys(labels):
        return {stress_key(stress_display(lbl, code_names)) for lbl in labels or ()}
    chain_keys, psyche_keys = keys(chain), keys(psyche)
    groups = {g: [] for g in BODY_GROUPS}
    for d in out["body"]:
        k = stress_key(d)
        group = ("Causal chain" if k in chain_keys else "Infoceuticals" if _info_match(d)
                 else "Psyche" if k in psyche_keys else "Body stresses")
        groups[group].append(d)
    out["body_groups"] = [[g, groups[g]] for g in BODY_GROUPS if groups[g]]
    out["body"] = [d for _g, items in out["body_groups"] for d in items]
    taken = {stress_key(d) for d in out["body"] + out["mind"]}
    heads = [h for h in layer_heads(cx, test_id)
             if stress_key(stress_display(h, code_names)) not in taken]
    out["spirit"] = natural_sorted(_display_list(
        spirit_stress_labels(cx, test_id) + heads, code_names, True))
    out["on_layer"] = natural_sorted(_display_list(
        on_layer_labels(cx, test_id), code_names, False))
    unnamed = set()
    for key in ("spirit", "mind", "body", "all"):
        unnamed |= {d for d in out[key] if _info_match(d) and ":" not in d}
    out["unnamed"] = sorted(unnamed, key=natural_key)
    return {k: out[k] for k in ("spirit", "mind", "body", "body_groups", "on_layer",
                                "all", "unnamed")}


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
# Glen, 15 November 2026: "BSI 21 times 3 to 1 times 1, location Liver."
# 14. A "location" cue straight after a BSI cue, with nothing between, is the BSI
#     location, not the terrain location: in the same sentence after the four numbers,
#     or the next sentence, or the next utterance (after_bsi carries it over). Any other
#     sentence between ends it, so "Terrain 3, location liver" and a "location" on its
#     own still fill the terrain location (rule 10). "Terrain 3, location liver" is
#     read as the phase, then the terrain location, as "Terrain 3. Location liver." is. After a BSI that was not
#     understood, the location is not saved either: the event says bsi_ok False.
#     Glen, the same day: "Or I could say '21 x 3 Liver to 1 x 1'". Words between the
#     second and third numbers are the BSI location too (parse_bsi_parts). Said both
#     ways in one BSI, the words between the numbers win, and the event carries the
#     other as "conflict" so the page can say so. Numbers without "BSI" are no BSI.
# 15. "The score is", "score is", "the score", "The BSI is" and "BSI is" at a cue
#     position are the "BSI" cue, with all its rules (Glen, 15 November 2026). "score"
#     anywhere else is not a cue.
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
# Glen, 15 November 2026: "BSI" ("BSI is", "The BSI is") or "The score is" ("score is",
# "the score"). The "is" after any of them is skipped by _hf_rest.
_HF_TERRAIN = re.compile(r"(terrain|location|(?:the\s+)?bsi|the\s+score(?:\s+is)?|score\s+is)\b",
                         re.I)
_HF_ALSO = re.compile(r"(?:it\s+)?also\s+balances?\b", re.I)
# Rule 14: "location" inside a BSI sentence, after the numbers.
_HF_BSI_LOC = re.compile(r"[\s,\-]*\blocation\b", re.I)
AFTER_BSI = ("ok", "bad")
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
    nums, _loc, why = parse_bsi_parts(text)
    return nums, why


# Rule 14 (a): words a BSI location may start with, dropped: "12 by 8 at the liver".
_BSI_LOC_LEAD = {"at", "in", "the", "on"}


def parse_bsi_parts(text):
    """([A, B, C, D], location, '') or (None, '', why) (rules 11 and 14).

    location is the words said between the second and third numbers, as Glen says
    "BSI 21 x 3 Liver to 1 x 1" (15 November 2026), '' when there are none. A leading
    "at", "in", "the" or "on" and the separators around it are dropped. Words anywhere
    else still mean the BSI is not understood."""
    from dashboard.terrain_phase import bsi_num
    s = str(text or "").replace("\u00d7", " x ")
    s = re.sub(r"(\d)\s*[xX]\s*(?=\d)", r"\1 x ", s)       # "12x8"
    s = re.sub(r"^(?:is|reads?|of)\s+", "", s.strip(), flags=re.I)
    raw = [w for w in re.split(r"[\s,.;:!?\-]+", s) if w]
    words = [w.lower() for w in raw]
    nums, loc = [], []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _BSI_SEPS:
            if len(nums) == 2 and loc:
                loc.append(raw[i])        # "liver and kidney"; trailing "to" goes below
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
        elif len(nums) == 2:
            if loc or w not in _BSI_LOC_LEAD:
                loc.append(raw[i])
        else:
            return None, "", f"'{w}' is not a number"
        i += 1
    if len(nums) != 4:
        return None, "", f"heard {len(nums)} number{'' if len(nums) == 1 else 's'}, not 4"
    bad = [n for n in nums if bsi_num(n) is None]
    if bad:
        return None, "", f"{bad[0]} is outside 1 to 21"
    while loc and loc[-1].lower() in _BSI_SEPS:
        loc.pop()
    return nums, " ".join(loc), ""


# Glen, 15 November 2026: Emary asks a spoken question back when a BSI is not
# understood. The question names what was heard ("I heard 21, 3 and 1. What is the
# fourth BSI number?") and the answer may be only the missing number ("the fourth is
# 1"), so a failed BSI also carries every number heard, in order, in range or not.
_BSI_ORDINALS = {"first", "second", "third", "fourth", "last", "other", "next"}


def bsi_heard(text):
    """(numbers, location): every number said, in order, whether in range or not, and
    the words between the second and third numbers (rule 14 (a)). Never fails: other
    words are skipped, and "one" straight after an ordinal ("the fourth one is 2") is
    not a number. Only used to word a question and to read its answer."""
    s = str(text or "").replace("\u00d7", " x ")
    s = re.sub(r"(\d)\s*[xX]\s*(?=\d)", r"\1 x ", s)
    raw = [w for w in re.split(r"[\s,.;:!?\-]+", s) if w]
    words = [w.lower() for w in raw]
    nums, loc = [], []
    i = 0
    while i < len(words):
        w = words[i]
        prev = words[i - 1] if i else ""
        if w in _BSI_SEPS:
            if len(nums) == 2 and loc:
                loc.append(raw[i])
        elif w.isdigit():
            nums.append(int(w))
        elif w in _TENS:
            n = _TENS[w]
            if i + 1 < len(words) and words[i + 1] in _UNITS_1_19 \
                    and 1 <= _UNITS_1_19[words[i + 1]] <= 9:
                n += _UNITS_1_19[words[i + 1]]
                i += 1
            nums.append(n)
        elif w in _UNITS_1_19 and not (w == "one" and prev in _BSI_ORDINALS):
            nums.append(_UNITS_1_19[w])
        elif len(nums) == 2 and (loc or w not in _BSI_LOC_LEAD):
            loc.append(raw[i])
        i += 1
    while loc and loc[-1].lower() in _BSI_SEPS:
        loc.pop()
    return nums, " ".join(loc)


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
        if not nums:                      # for the question back (bsi_heard)
            ev["heard"], ev["heard_location"] = bsi_heard(words)
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


def segment_utterance(text, pending=None, after_remedy=False, after_bsi=None):
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
    returned, and the page sends them back. Pure: no database, no side effects.

    after_bsi is "ok" or "bad" when the last thing said was a BSI (understood or not),
    so a "location" said next is the BSI location (rule 14): that event carries
    "of": "bsi" and "bsi_ok". It carries over like pending, and the result holds the
    key only while it is set."""
    pending = pending if pending in PENDING else None
    after_remedy = bool(after_remedy)
    after_bsi = after_bsi if after_bsi in AFTER_BSI else None
    events = []

    def bsi_events(words, loc_b=None):
        """The BSI event, then its location event if one was said (rule 14):
        (a) between the numbers, or (b) after them as "location X". Both: (a) wins
        and the event carries (b) as "conflict". Returns after_bsi for what follows."""
        nonlocal pending
        ev = _terrain_event("bsi", words)
        events.append(ev)
        state = "ok" if ev["value"] else "bad"
        loc_a = parse_bsi_parts(words)[1] if ev["value"] else ""
        if loc_a:
            le = {"terrain": "location", "text": loc_a, "of": "bsi", "bsi_ok": True}
            if loc_b:
                le["conflict"] = loc_b
            events.append(le)
            return None
        if loc_b:
            events.append({"terrain": "location", "text": loc_b, "of": "bsi",
                           "bsi_ok": state == "ok"})
            return None
        if loc_b is not None:
            pending = "location"          # "..., location." the words come next
        return state

    def location_event(words):
        ev = _terrain_event("location", words)
        if after_bsi:
            ev["of"] = "bsi"
            ev["bsi_ok"] = after_bsi == "ok"
        return ev

    def field(kind, words, both=False):
        events.append({"field": kind, "text": words, "both": both})

    for sent in _hf_sentences(text):
        s = sent
        keep_bsi = False                  # rule 14: only a location cue keeps it
        while s:
            s = s[_HF_FILLER.match(s).end():].strip(" ,-")
            if not s:
                keep_bsi = True           # "Okay." alone is nothing between
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
                if "score" in cue or "bsi" in cue:
                    cue = "bsi"           # rule 15: "the BSI", "the score" are "BSI"
                rest = _hf_rest(s, m)
                loc = None
                if cue in ("bsi", "terrain") and rest:
                    lm = _HF_BSI_LOC.search(rest)
                    if lm:                # "BSI 21 by 3 to 1 by 1, location Liver"
                        loc = _hf_rest(rest, lm)
                        rest = rest[:lm.start()].strip(" ,-")
                if cue == "location" and rest:
                    events.append(location_event(rest))
                    pending, after_bsi = None, None
                elif cue == "location":
                    pending = cue
                    keep_bsi = True       # the words come next, still the BSI's
                elif cue == "bsi" and (rest or loc is not None):
                    pending = None
                    after_bsi = bsi_events(rest, loc)
                    keep_bsi = True
                elif cue == "terrain" and loc is not None:
                    # "Terrain 3, location liver": the phase, then the terrain location.
                    if rest:
                        events.append(_terrain_event(cue, rest))
                    if loc:
                        events.append(_terrain_event("location", loc))
                        pending = None
                    else:
                        pending = "location"
                elif rest:
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
                if pending == "location":
                    events.append(location_event(s))
                    after_bsi = None
                elif pending == "bsi":
                    pending = None
                    lm = _HF_BSI_LOC.search(s)
                    after_bsi = bsi_events(s[:lm.start()].strip(" ,-"), _hf_rest(s, lm)) \
                        if lm else bsi_events(s)
                    keep_bsi = True
                    break
                else:
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
        if not keep_bsi:
            after_bsi = None
    out = {"events": events, "pending": pending, "after_remedy": after_remedy}
    if after_bsi:
        out["after_bsi"] = after_bsi
    return out


def handsfree_events(cx, text, pending=None, after_remedy=False, test_id=None,
                     after_bsi=None):
    """segment_utterance, with each Head, Tail or Remedy event matched the way a short
    recording is (match_term, the cue words already off). Writes nothing: the page
    applies the events, in order, through the row routes, as hands-free rows."""
    out = segment_utterance(text, pending, after_remedy, after_bsi)
    for ev in out["events"]:
        kind = ev.get("field")
        if kind in KINDS:
            ev["match"] = match_term(cx, kind, ev["text"], test_id=test_id, cues=False)
        elif ev.get("terrain") == "location" or ev.get("also_balances"):
            # Rules 10 and 14: the stress list the layer cards use, this client's first.
            ev["match"] = match_term(cx, "head", ev["text"], test_id=test_id, cues=False)
    return out
