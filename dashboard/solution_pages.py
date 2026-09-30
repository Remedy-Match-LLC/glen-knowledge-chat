"""Solution category pages: /solutions/ and /solutions/<slug>.

Pure, no Flask. One data file, data/solution_categories.json, names each category's
principle, products and learn pages. Product details are read from the catalogue at
render time through the caller's `get_product` (app._get_product), so a retired product
follows its successor exactly as the product page does, and an inactive one drops out.
"""
import json
import re
from pathlib import Path

from dashboard.related_products import DO_NOT_RECOMMEND

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "solution_categories.json"

# Glen: never recommend the Living Water prill-bead bottle or its filter refill. Neither
# is in the catalogue today; this stops a re-added listing from entering a category. The
# "(Living Water)" plate ionizers are the primary recommendation, so the brand alone
# must never match.
EXCLUDED_NAME_PARTS = ("prill", "living water bottle", "living water filter")

# The same flags the store leaves out (dashboard.shop_catalog.EXCLUDED_FLAGS).
_INACTIVE_FLAGS = ("inactive", "info_only", "service")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load(path=None):
    with open(path or DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories")
    if not isinstance(cats, list):
        raise ValueError("solution_categories.json has no categories list")
    return cats


def is_excluded(slug, product):
    if slug in DO_NOT_RECOMMEND:
        return True
    name = ((product or {}).get("name") or "").lower()
    return any(part in name for part in EXCLUDED_NAME_PARTS)


def problems(categories, products, table_category_names):
    out, seen = [], set()
    for c in categories:
        slug = c.get("slug") or ""
        if not _SLUG_RE.match(slug):
            out.append(f"Category slug {slug!r} is not a lowercase-hyphen slug.")
        if slug in seen:
            out.append(f"Category slug {slug!r} appears twice.")
        seen.add(slug)
        for p in c.get("products") or []:
            rec = products.get(p)
            if rec is None:
                out.append(f"{slug}: product {p!r} is not in the catalogue.")
            elif any(rec.get(f) for f in _INACTIVE_FLAGS):
                out.append(f"{slug}: product {p!r} is not active.")
            if is_excluded(p, rec):
                out.append(f"{slug}: product {p!r} is on the do-not-recommend list.")
        if not c.get("products") and not c.get("learn"):
            out.append(f"{slug}: has no products and no learn page.")
    for name in table_category_names:
        n = sum(name in (c.get("table_names") or []) for c in categories)
        if n == 0:
            out.append(f"Remedy table name {name!r} is in no category.")
        elif n > 1:
            out.append(f"Remedy table name {name!r} is in {n} categories.")
    return out
