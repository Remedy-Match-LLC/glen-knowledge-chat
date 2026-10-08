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


_DASHES = re.compile("[\u2010-\u2015\u2212-]")


def _normalize(name):
    """Lowercase and strip common descriptors so aliases match real labels."""
    s = (name or "").lower()
    for cut in ("(vegetable source)", "(vegetable)", "(as a flow agent)", "(from rice)"):
        s = s.replace(cut, "")
    # Collapse intra-word hyphens to spaces so a hyphenated label (e.g.
    # "Magnesium-Stearate") still matches the space-joined alias
    # ("magnesium stearate"). The word-boundary regex in _hits treats "-" as
    # a delimiter, so without this the hyphenated form would silently miss.
    s = _DASHES.sub(" ", s)
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
    # "free of hypromellose phthalate" -- a negated phthalate name of up to three words.
    s = re.sub(r"\b(?:free of|free from|without)\s+(?:[a-z0-9]+\s+){0,3}phthalates?\b", " ", s)
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
    plural = "s?" if len(alias) > 4 else ""
    return re.search(r"(?<![a-z0-9])" + re.escape(alias) + plural + r"(?![a-z0-9])", text) is not None


# "non phthalate", "no phthalates", "free of/from phthalates", "without phthalates", "phthalates free"
# (hyphens are already spaces). A denial like "not phthalate free" is not matched by any of these.
_PHTHALATE_FREE = re.compile(
    r"\b(?:non|no)\s+phthalates?\b"
    r"|\b(?:without|free of|free from)\s+(?:[a-z0-9]+\s+){0,3}?phthalates?\b"
    r"|\bphthalates?\s+free\b")


def _groups(other_ingredients):
    """Re-join items the splitter cut inside brackets ("DRcaps (hypromellose", "gellan gum)"),
    so each item maps to the bracket group it came from. Returns one group index per item."""
    out, depth, g = [], 0, -1
    for raw in other_ingredients or []:
        if depth <= 0:
            g += 1
            depth = 0
        out.append(g)
        r = raw or ""
        depth += r.count("(") + r.count("[") - r.count(")") - r.count("]")
    return out


def _exempt_text(items):
    """A bracket group's text, normalised with negations KEPT ("phthalate free" stays visible),
    then with denials removed ("no DRcaps", "not DRcaps", "non DRcaps"), so only an affirmed
    exemption counts."""
    s = _DASHES.sub(" ", " ; ".join(items).lower())
    s = _PHTHALATE_FREE.sub(" phthalate free ", s)
    s = re.sub(r"\b(?:no|not|non|without)\s+[a-z0-9]+", " ", s)
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
    groups = _groups(other_ingredients)
    for i, raw in enumerate(other_ingredients):
        norm = _normalize(raw)
        own = _exempt_text([x for x, g in zip(other_ingredients, groups) if g == groups[i]])
        if _hits(norm, avoidlist["red"], own):
            red_hits.append(raw)
        elif _hits(norm, avoidlist["yellow"], own):
            yellow_hits.append(raw)
    if red_hits:
        color = "red"
    elif yellow_hits:
        color = "yellow"
    else:
        color = "green"
    return {"color": color, "red_hits": red_hits, "yellow_hits": yellow_hits,
            "avoidlist_version": version}
