"""Glen's 2026-11-09 catalogue rulings: eight new entries at the prices he set."""
import json
from pathlib import Path

from dashboard.shipping import PROD_BOTTLE_NAMES

PRODUCTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "products.json").read_text())["products"]

EXPECTED = {
    "miron-violet-glass-water-bottle-1l": 4000,
    "microwater-alkaline": 300,
    "miracule-water": 300,
    "capsule-filling-machine-100m": 19700,
    "capsule-filling-machine-120s": 19700,
    "bioenergetic-wellness-scanner-usb": 20000,
    "bioenergetic-wellness-scanner-bluetooth": 10000,
    "bacterotox-homeopathic-complex-in-terrain-restore": 7000,
}


def test_new_entries_carry_glens_prices():
    for slug, cents in EXPECTED.items():
        assert PRODUCTS[slug]["price_cents"] == cents, slug


def test_new_entries_have_a_bottle_type_prod_knows():
    # A missing or unknown bottle_type drops the whole cart to the coarse shipping rule.
    for slug in EXPECTED:
        assert PRODUCTS[slug]["bottle_type"] in PROD_BOTTLE_NAMES, slug


def test_old_entries_kept_so_links_still_work():
    assert PRODUCTS["capsule-filling-machine"]["price_cents"] == 19700
    assert "bioenergetic-wellness-scanner" in PRODUCTS
    assert PRODUCTS["biofield-analysis"]["price_cents"] == 30000
