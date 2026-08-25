import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _active_catalog():
    products = json.loads((ROOT / "data/products.json").read_text())["products"]
    return {
        slug: product for slug, product in products.items()
        if product and not product.get("inactive") and not product.get("info_only")
    }


def test_practice_recommendations_use_active_catalog_slugs():
    active = _active_catalog()
    configured = json.loads(
        (ROOT / "data/practice_recommendations.json").read_text())
    invalid = []
    for group, rows in configured.items():
        if group.startswith("_"):
            continue
        invalid.extend((group, row.get("slug")) for row in rows
                       if row.get("slug") not in active)
    assert invalid == []


def test_upsell_pairings_use_active_sources_and_exact_catalog_names():
    active = _active_catalog()
    names = {(product.get("name") or "").strip().lower()
             for product in active.values()}
    pairings = json.loads(
        (ROOT / "data/upsell-pairings.json").read_text())["pairings"]
    invalid_sources = [slug for slug in pairings if slug not in active]
    invalid_targets = [name for values in pairings.values() for name in values
                       if name.strip().lower() not in names]
    assert invalid_sources == []
    assert invalid_targets == []
