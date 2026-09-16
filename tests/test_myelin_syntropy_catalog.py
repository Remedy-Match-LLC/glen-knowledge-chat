import json
import pathlib
from pathlib import Path


CATALOG = Path(__file__).parents[1] / "data" / "products.json"


def test_myelin_syntropy_is_a_sellable_functional_formulation():
    product = json.loads(CATALOG.read_text())["products"]["myelin-syntropy"]

    # Renamed 2026-09-16 on Glen's instruction, to match the FileMaker record he
    # created as "Myelin Syntropy Powder" — his convention for powders, and the name
    # the invoice bottle-count lookup matches on. The slug is deliberately unchanged.
    assert product["name"] == "Myelin Syntropy Powder"
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


def test_neuroceramides_is_retired_now_that_the_sell_through_has_ended():
    """Renamed from ..._remains_active_for_one_bottle_sell_through on 2026-09-16.

    The old name pinned a state that has now ended. Glen: "Neuroceramides is already out
    of stock and Myelin is produced." So remaining_inventory_units is 0 as a real new
    value rather than a status flip, and the discontinue-when-sold-out condition the
    2026-08-24 transition note set is met.

    WHY `inactive` IS THE POINT, and not tidiness. dashboard.products.catalog() skips any
    entry carrying `inactive`, and the report builder's layers[].alternatives[] is built
    from that catalog. Setting it is the only thing that stops Neuroceramides being
    offered as an alternative on newly built reports: clinical swapped three findings and
    five rebuilt drafts still named it, because the name never came from the findings.

    BOTH fields, never one. _get_product resolves superseded_by FIRST and only then checks
    inactive, so a dead slug reaches its live twin. Its own docstring says an inactive
    record with no successor resolves to None, which dead-ends storefront links and order
    history.
    """
    products = json.loads(CATALOG.read_text())["products"]

    for slug in ("neuroceramides", "myelin-repair-neuroceramides"):
        legacy = products[slug]
        assert legacy["remaining_inventory_units"] == 0
        assert legacy["discontinue_when_sold_out"] is True
        assert legacy["successor_slug"] == "myelin-syntropy"
        assert legacy["inactive"] is True
        assert legacy["superseded_by"] == "myelin-syntropy"


def test_a_retired_neuroceramides_slug_still_reaches_a_live_product():
    """An old storefront link or order line must price, not dead-end."""
    from dashboard import products as pm
    products = json.loads(CATALOG.read_text())["products"]
    for slug in ("neuroceramides", "myelin-repair-neuroceramides"):
        target = pm.superseded_slug(slug, products)
        assert target == "myelin-syntropy"
        assert not products[target].get("inactive")


def test_neither_legacy_slug_is_offered_as_an_alternative_any_more(monkeypatch):
    """The behaviour the retirement exists for.

    DATA_DIR is cleared first. `_products_path()` prefers DATA_DIR/products.json when one
    exists, so without this the assertion reads whatever an earlier test happened to write
    to the shared data directory rather than the file this PR changes. The first version
    of this test did exactly that and passed or failed depending on run order.
    """
    from dashboard import products as pm
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert pathlib.Path(pm._products_path()) == CATALOG, (
        "this test must read the repo catalog, not a scratch copy"
    )
    offered = {c["slug"] for c in pm.catalog(with_ingredients_only=False)}
    assert "neuroceramides" not in offered
    assert "myelin-repair-neuroceramides" not in offered
    assert "myelin-syntropy" in offered, "the successor must still be offerable"


def test_the_successor_carries_glens_confirmed_name():
    products = json.loads(CATALOG.read_text())["products"]
    assert products["myelin-syntropy"]["name"] == "Myelin Syntropy Powder"
