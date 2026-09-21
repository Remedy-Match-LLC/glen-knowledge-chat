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
