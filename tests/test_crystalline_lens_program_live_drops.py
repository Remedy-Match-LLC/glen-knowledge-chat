"""The Crystalline Lens Program packs the live Clear Lens Eye Drops listing.

It named `clear-lens-eye-drops-aces-cat-eye-drops`, the retired listing, so packing
expanded the program to an inactive record. Glen "yes", platform tab, 2026-11-03:
repoint that one component to `clear-lens-eye-drops`. Both are $70, so the program's
price rule (components less 10%) and its $315 price are unchanged.
"""
import json

PRODUCTS = json.load(open("data/products.json"))["products"]
PROGRAM = PRODUCTS["crystalline-lens-program"]


def test_the_program_names_the_live_drops():
    slugs = [c["slug"] for c in PROGRAM["bundle_component_slugs"]]
    assert slugs == ["crystalline-clarity", "clear-lens-eye-drops", "clarity",
                     "golden-book", "crucifer-complex"]
    assert not PRODUCTS["clear-lens-eye-drops"].get("inactive")


def test_no_component_of_the_program_is_retired():
    for c in PROGRAM["bundle_component_slugs"]:
        assert not PRODUCTS[c["slug"]].get("inactive"), c["slug"]


def test_the_price_is_unchanged():
    assert PROGRAM["price_cents"] == 31500
    assert PROGRAM["price_rule"] == "components_less_10pct"
    parts = sum(PRODUCTS[c["slug"]]["price_cents"] * c.get("qty", 1)
                for c in PROGRAM["bundle_component_slugs"])
    assert parts == 35000
