"""Which E4L infoceutical covers each scan code, for an animal's scan.

Glen, 2026-09-18: the automated interpretation of an animal's scan recommends the
E4L infoceuticals, never Functional Formulations, using "the names you are currently
using: they name functions".

The sellable product is the only reliable name. e4l_items calls ES1 "Lymph Star",
but the product is "ES1 Immune Energetic Star Infoceutical", so a name built from
e4l_items would miss the catalog. Each infoceutical product is named with its code
first. On 2026-09-21 the catalog held exactly one active product for each of the 72
ED, EI, ES, ET and MB codes, and none for the ER, MR, BFA, ENV or NUT codes. A code
with no product stays uncovered for an animal: it never falls back to an FF.
"""
import re

_CODE_FIRST = re.compile(r"^([A-Z]{2,3}\d{1,3})\s")


def infoceutical_by_code(catalog):
    """{code: product name} from a slug-keyed catalog (data/products.json 'products').

    Only active products whose name starts with a code followed by a space AND says
    "Infoceutical". A retired product is never proposed. When two products claim one
    code, the first in slug order wins, so the result does not depend on dict order."""
    out = {}
    for slug in sorted(catalog or {}):
        item = catalog[slug]
        if not isinstance(item, dict) or item.get("inactive"):
            continue
        name = (item.get("name") or "").strip()
        m = _CODE_FIRST.match(name)
        if m and "infoceutical" in name.lower():
            out.setdefault(m.group(1), name)
    return out


# One chain line can hold several remedies: "Immune Modulation + ES1 ... Infoceutical".
# The same separators dashboard.biofield_portal_publish._remedy_keys splits on, plus a
# comma. Product names themselves hold "/", "&" and ":", so the whole name is tried first.
_SEPARATORS = re.compile(r"\s\+\s|\+|;|\n|,")


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def infoceutical_names(catalog=None, product_names=()):
    """The set of infoceutical product names (normalised keys) an animal may be given.

    Every product, active or not, in the slug-keyed catalog and in `product_names` (the
    FileMaker remedy list) whose name says "Infoceutical". That is the whole-name list:
    12 of the 84 in FileMaker carry no code ("Day Infoceutical"), so a code is not
    required, but a made-up name that merely contains the word is not on it."""
    out = set()
    items = (catalog or {}).values() if isinstance(catalog, dict) else (catalog or [])
    names = [(i.get("name") if isinstance(i, dict) else i) for i in items]
    for name in list(names) + list(product_names or ()):
        name = (name or "").strip()
        if name and re.search(r"\binfoceutical\b", name, re.I):
            out.add(_key(name))
    out.discard("")
    return out


_DEFAULT_KNOWN = None


def _default_known():
    """The repo catalog's infoceuticals, read once. The app passes its own list, which
    adds the FileMaker remedy list."""
    global _DEFAULT_KNOWN
    if _DEFAULT_KNOWN is None:
        try:
            from dashboard.biofield_portal_publish import load_catalog
            _DEFAULT_KNOWN = infoceutical_names(load_catalog())
        except Exception:
            return set()
    return _DEFAULT_KNOWN


def is_infoceutical_remedy(name, known=None):
    """True when a remedy name is an E4L infoceutical, the only thing an animal gets.

    Glen, 2026-11-06, of Skylar Verches, a dog: "It's also recommending FF's instead of
    just infoceuticals." The one product test every animal surface uses.

    The whole name must be on the infoceutical list (`known`, from infoceutical_names;
    the repo catalog when not given). A line naming several remedies passes only when
    every one of them is an infoceutical: "Immune Modulation + ES1 Immune Energetic
    Star Infoceutical" holds an FF, so it is refused."""
    known = _default_known() if known is None else known
    whole = _key(name)
    if not whole:
        return False
    if whole in known:
        return True
    parts = [_key(p) for p in _SEPARATORS.split(name or "")]
    parts = [p for p in parts if p]
    return len(parts) > 1 and all(p in known for p in parts)
