"""The sunflower phosphatides raw is 30%, per Glen reading the bag on 2026-11-10.

FileMaker raw 5009 says 30% too. "50%" had been copied from label to label into 16 live
products and 10 more in the enrichment source.
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
FILES = ("products.json", "products-manual-corrections.json", "products-enrich-clean.json")


def test_no_catalogue_file_says_phosphatides_50_percent():
    for name in FILES:
        text = (DATA / name).read_text()
        assert not re.search(r"phosphatides 50", text, re.I), name


def test_the_two_listed_lines_read_30_percent_at_13_and_21_mg():
    live = json.loads((DATA / "products.json").read_text())["products"]
    pin = json.loads((DATA / "products-manual-corrections.json").read_text())
    for entry, dose in ((live["vitreous-vitality"], "13 mg"), (pin["vitreous-vitality"], "13 mg"),
                        (live["stone-solvent"], "21 mg")):
        by = {i["name"]: i["dose"] for i in entry["ingredients"]}
        assert by["Phosphatides 30% (Helianthus annuus)"] == dose


def test_every_phosphatides_row_lands_on_a_page_another_product_already_uses():
    from dashboard.ingredients import slugify
    live = json.loads((DATA / "products.json").read_text())["products"]
    uses = {}
    for slug, p in live.items():
        for ing in p.get("ingredients") or []:
            if isinstance(ing, dict) and "hosphatides 30%" in ing["name"]:
                uses.setdefault(ing.get("page_slug") or slugify(ing["name"]), set()).add(slug)
    assert uses and all(len(products) >= 2 for products in uses.values()), uses
