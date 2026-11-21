"""Glen's 2026-11-20 ruling: Quinton Eye Drops, $40, fixed like other single-ingredient bottles, SRP $80."""
import json
from pathlib import Path

from dashboard.shipping import PROD_BOTTLE_NAMES

PRODUCTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "products.json").read_text())["products"]
SLUG = "quinton-eye-drops"


def test_price_and_srp():
    p = PRODUCTS[SLUG]
    assert p["price_cents"] == 4000
    assert p["regular_cents"] == 8000


def test_fixed_like_single_ingredient_bottles():
    # Same pricing shape as the $40 single-ingredient powders: no remedy volume curve.
    p = PRODUCTS[SLUG]
    assert p["qty_pricing"] is False
    assert p["volume_eligible"] is False


def test_cart_price_stays_40_at_any_quantity():
    # Without volume_eligible false, the cart engine treats a non-"pure powder" name as
    # volume-eligible and discounts 2+ bottles. Glen ruled the price fixed.
    import app
    item = app._engine_item(dict(PRODUCTS[SLUG], slug=SLUG), 5)
    assert item["volume_eligible"] is False
    assert item["unit_cents"] == 4000


def test_ships_as_the_5ml_dropper():
    # A missing or unknown bottle_type drops the whole cart to the coarse shipping rule.
    assert PRODUCTS[SLUG]["bottle_type"] == "Dropper 5 mL"
    assert PRODUCTS[SLUG]["bottle_type"] in PROD_BOTTLE_NAMES


def test_ingredient_is_quinton_isotonic():
    assert [i["name"] for i in PRODUCTS[SLUG]["ingredients"]] == ["Quinton Isotonic"]
    assert PRODUCTS[SLUG]["no_groovekart"] is True
