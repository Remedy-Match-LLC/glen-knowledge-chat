import json
from pathlib import Path


CATALOG = Path(__file__).parents[1] / "data" / "products.json"


def test_myelin_syntropy_is_a_sellable_functional_formulation():
    product = json.loads(CATALOG.read_text())["products"]["myelin-syntropy"]

    assert product["name"] == "Myelin Syntropy"
    assert product["price_cents"] == 6997
    assert product["qty_pricing"] is True
    assert product["bottle_type"] == "120 caps"
    assert product["net_weight_g"] == 106.7
    assert product["serving_size_g"] == 3.5
    assert product["servings_per_container"] == 30
    assert product["directions"].startswith("Mix up to 1 scoop")


def test_myelin_syntropy_formula_matches_the_approved_label():
    product = json.loads(CATALOG.read_text())["products"]["myelin-syntropy"]
    ingredients = {item["name"]: item for item in product["ingredients"]}

    assert ingredients["N-Acetyl-D-Glucosamine"]["compound_mg"] == 1974.2
    assert ingredients["Creatine Monohydrate"]["compound_mg"] == 984.4
    magnesium_taurate = ingredients["Magnesium Taurate"]
    assert magnesium_taurate["compound_mg"] == 541.4
    assert magnesium_taurate["standardized_pct"] == 8.9
    assert magnesium_taurate["active_mg"] == 48.2


def test_neuroceramides_remains_active_for_one_bottle_sell_through():
    products = json.loads(CATALOG.read_text())["products"]

    for slug in ("neuroceramides", "myelin-repair-neuroceramides"):
        legacy = products[slug]
        assert legacy["remaining_inventory_units"] == 1
        assert legacy["sell_through"] is True
        assert legacy["discontinue_when_sold_out"] is True
        assert legacy["successor_slug"] == "myelin-syntropy"
        assert legacy.get("inactive") is not True
