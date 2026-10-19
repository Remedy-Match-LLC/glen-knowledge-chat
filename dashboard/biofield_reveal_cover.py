"""Which reveal layers the Clinical layers already cover (Glen, 2026-10-18).

"It should add any of the BWS layers that are not already covered by a related
Clinical layer." Covered means, asked what counts: the same condition heading, the
same remedy, "or one of the 5 recommended infoceuticals that already shows as ticked
in one of the Clinical layers".

Every layer already on the chain counts as a Clinical layer: the chain's origin
column cannot tell them apart (Josie's are "live").

The heading rule, from Glen's examples: a heading and a condition are each split on
"and" into parts. A condition part matches when all its words are in one part of the
heading, leaving out generic words. Where two condition parts fit, the longer wins,
counting every condition in the shared catalog and not only this client's: "Blood
Sugar Regulation" is Blood sugar, never Blood and circulation, even for a client
with no blood sugar layer. "Blood Regulation" is not Blood sugar.
"""
import re

GENERIC = frozenset({"the", "with", "for", "health", "support", "function", "functions",
                     "system", "systems", "balance", "stress", "energy", "general",
                     "regulation"})


def _parts(text):
    return [p for p in re.split(r"\s+and\s+|\s*&\s*|\s*,\s*|\s*/\s*", str(text or "").lower())
            if p.strip()]


def _stem(word):
    """Singular, so "Allergies" meets "Allergy" and "Parasites" meets "Parasite"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _words(part):
    return frozenset(_stem(w) for w in re.split(r"[^a-z]+", part)
                     if len(w) >= 3 and w not in GENERIC)


def heading_match(title, conditions, vocabulary=()):
    """(condition, matching words) for the reveal heading `title`, or None.
    `vocabulary` is every known condition name, so a longer phrase the client does
    not have still outranks a shorter one the client does."""
    units = [(c, _words(p)) for c in conditions for p in _parts(c) if _words(p)]
    known = [_words(p) for c in vocabulary for p in _parts(c) if _words(p)]
    found = []
    for part in _parts(title):
        have = _words(part)
        fits = [(c, u) for c, u in units if u <= have]
        if fits:
            longest = max(len(u) for u in [u for _, u in fits] + [k for k in known if k <= have])
            found += [(c, u) for c, u in fits if len(u) == longest]
    if not found:
        return None
    condition, unit = found[0]
    return condition, " ".join(sorted(unit))


def ticked_codes(remedy_names):
    """E4L codes of the infoceuticals among `remedy_names`."""
    from dashboard.biofield_clinical_sections import infoceutical_code
    return {c for c in (infoceutical_code(n) for n in remedy_names) if c}


def covered(layer, conditions, remedies, recommended, ticked, vocabulary=()):
    """(rule, why) when a Clinical layer covers this reveal layer, else None.

    `conditions` are the existing layers' headings, `remedies` their remedy names in
    lower case, `recommended` the scan's recommended codes (None when unread), and
    `ticked` the codes of infoceuticals ticked in a Clinical layer."""
    match = heading_match(layer.get("title"), conditions, vocabulary)
    if match:
        return "same heading", f"{match[0]} (shares \"{match[1]}\")"
    remedy = str(layer.get("remedy_name") or "").strip().lower()
    if remedy and remedy in remedies:
        return "same remedy", layer.get("remedy_name")
    from dashboard.biofield_authoring import is_infoceutical_code
    codes = [str(c).strip().upper() for c in layer.get("codes") or []
             if is_infoceutical_code(str(c).strip().upper())]
    hit = sorted(c for c in codes if recommended and c in recommended and c in ticked)
    if hit:
        return "recommended infoceutical already ticked", ", ".join(hit)
    return None


def plan(layers, existing_rows, recommended, ticked_names, resolve=None, vocabulary=()):
    """{"add": [layer], "skip": [{"layer", "rule", "why"}], "recommended_unread": bool}.

    `existing_rows` are the chain's rows ({head, most_affected, remedy}). `resolve`
    turns a reveal remedy into the name the chain stores, as the import does."""
    # A Clinical row names its condition in most_affected; a scan row holds the
    # scan's comma-joined labels there, which are not conditions.
    conditions = sorted({str(r.get(k) or "").strip() for r in existing_rows or []
                         for k in (("head",) if r.get("origin") == "scan"
                                   else ("head", "most_affected"))} - {""})
    remedies = {str(r.get("remedy") or "").strip().lower() for r in existing_rows or []} - {""}
    ticked = ticked_codes(list(ticked_names or []) + [r.get("remedy") for r in existing_rows or []])
    out = {"add": [], "skip": [], "recommended_unread": recommended is None}
    for layer in layers or []:
        probe = dict(layer)
        if resolve and probe.get("remedy_name"):
            probe["remedy_name"] = resolve(probe["remedy_name"]) or probe["remedy_name"]
        hit = covered(probe, conditions, remedies, recommended, ticked, vocabulary)
        if hit:
            out["skip"].append({"layer": layer, "rule": hit[0], "why": hit[1]})
        else:
            out["add"].append(layer)
    return out
