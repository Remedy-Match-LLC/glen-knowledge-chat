"""Role-aware excipient screen. Screens ONLY the Other Ingredients against the
avoid-list; the actives list is accepted for interface symmetry but is never
consulted (a substance's role decides -- silica as a nutrient is not a filler).
Pure: no Flask, no app import.

Contract for `other_ingredients`:
  None -> no excipient data was obtained -> color 'unrated' (NEVER green).
  []   -> the product is known to list no other ingredients -> 'green'.
  list -> screened item by item.
"""
import re


def _normalize(name):
    """Lowercase and strip common descriptors so aliases match real labels."""
    s = (name or "").lower()
    for cut in ("(vegetable source)", "(vegetable)", "(as a flow agent)", "(from rice)"):
        s = s.replace(cut, "")
    # Collapse intra-word hyphens to spaces so a hyphenated label (e.g.
    # "Magnesium-Stearate") still matches the space-joined alias
    # ("magnesium stearate"). The word-boundary regex in _hits treats "-" as
    # a delimiter, so without this the hyphenated form would silently miss.
    s = s.replace("-", " ")
    s = " ".join(s.split()).strip()
    return _strip_negations(s)


def _strip_negations(s):
    """Remove absence-declaring segments before alias matching, so a phrase
    that DECLARES the excipient is absent (marketing language on a clean
    product) doesn't get flagged as if the excipient were present. Runs
    after hyphens are already collapsed to spaces. Conservative by design:
    each pattern only drops the single adjacent word, which is enough to
    break a multi-word alias (e.g. removing "hydrogenated" from "non
    hydrogenated palm oil" leaves "palm oil", which no longer matches the
    "hydrogenated palm oil" alias).
    """
    # "free of gelatin" -- explicit "free of X" phrasing.
    s = re.sub(r"\bfree of ([a-z0-9]+)\b", " ", s)
    # "non gelatin" / "non hydrogenated" -- "non" prefix (already
    # space-separated by the hyphen collapse above).
    s = re.sub(r"\bnon ([a-z0-9]+)\b", " ", s)
    # "no gelatin" -- bare "no X" phrasing.
    s = re.sub(r"\bno ([a-z0-9]+)\b", " ", s)
    # "gelatin free" -- "X free" suffix phrasing.
    s = re.sub(r"\b([a-z0-9]+) free\b", " ", s)
    return " ".join(s.split()).strip()


def _has(text, alias):
    return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text) is not None


def _label_text(other_ingredients):
    """The whole label, normalised but with negations KEPT, so "phthalate free" is visible.
    The splitter cuts inside brackets ("DRcaps (hypromellose", "gellan gum)"), so an
    exemption has to be read across the label, not from one item."""
    s = " ; ".join(other_ingredients or []).lower().replace("-", " ")
    return " ".join(s.split())


def _hits(normalized_item, entries, label_text=""):
    for e in entries:
        if any(_has(label_text, u) for u in e.get("unless_label") or []):
            continue
        for alias in e["aliases"]:
            if _has(normalized_item, alias):
                return True
    return False


def screen_label(actives, other_ingredients, avoidlist):
    version = avoidlist.get("version", "")
    if other_ingredients is None:                       # no data -> unrated, never green
        return {"color": "unrated", "red_hits": [], "yellow_hits": [],
                "avoidlist_version": version}
    red_hits, yellow_hits = [], []
    label = _label_text(other_ingredients)
    for raw in other_ingredients:
        norm = _normalize(raw)
        if _hits(norm, avoidlist["red"], label):
            red_hits.append(raw)
        elif _hits(norm, avoidlist["yellow"], label):
            yellow_hits.append(raw)
    if red_hits:
        color = "red"
    elif yellow_hits:
        color = "yellow"
    else:
        color = "green"
    return {"color": color, "red_hits": red_hits, "yellow_hits": yellow_hits,
            "avoidlist_version": version}
