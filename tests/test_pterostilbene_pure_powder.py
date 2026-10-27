"""Pterostilbene Pure Powder, listed as a Pure Powder at $40.

Glen, 2026-10-26, production tab: "add it to the list while you work on the sales page
in parallel ... or after it's on the list for me now." He was invoicing a jar that day.
The intro and description wait for his copy approval, so the entry carries neither.
Spec: production/05 Formulations/_store-updates/2026-10-26-pterostilbene-pure-powder.md.
"""
import json
import pathlib

import pytest

from dashboard.pure_powders import PURE_POWDER_NAMES

REPO = pathlib.Path(__file__).resolve().parent.parent
KEY = "pterostilbene-pure-powder"
NAME = "Pterostilbene Pure Powder"


def _product():
    catalog = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))
    return dict(catalog["products"][KEY], slug=KEY)


def test_the_key_is_what_the_store_makes_from_the_name():
    import app as a
    assert a._slugify_product(NAME) == KEY


def test_the_entry_carries_the_label_fields_and_no_prose():
    p = _product()
    assert (p["name"], p["price_cents"], p["fmp_id"], p["bottle_type"]) == (
        NAME, 4000, "1204", "30 g")
    # The words on the jar label Glen printed 2026-10-26.
    assert p["ingredients"] == [{"name": "Pterostilbene 98% (trans-Pterostilbene)",
                                 "dose": "48 mg"}]
    assert p["directions"] == "Take 4 orange scoops 1 to 2 times daily, or as guided."
    assert p["miron_glass"] is False
    assert p["copy_pinned"] == ["ingredients", "research", "directions", "benefits"]
    for left_out in ("intro", "description", "how_it_works", "regular_cents", "qbo_item_id"):
        assert left_out not in p, left_out


def test_the_matcher_never_recommends_it():
    import app as a
    assert a._ff_auto_excluded(NAME) and a._ff_auto_excluded(NAME.lower())


def test_the_matcher_exclusion_rests_on_the_name_list(monkeypatch):
    import app as a
    from dashboard import pure_powders
    monkeypatch.setattr(pure_powders, "PURE_POWDER_NAMES",
                        PURE_POWDER_NAMES - {"pterostilbene pure powder"})
    monkeypatch.setattr(pure_powders, "PURE_POWDER_SLUGS",
                        pure_powders.PURE_POWDER_SLUGS - {KEY})
    assert not a._ff_auto_excluded(NAME)


def test_checkout_gives_no_volume_curve_and_the_pure_powder_floor():
    import app as a
    item = a._engine_item(_product(), 3)
    assert item["volume_eligible"] is False
    assert item["product"]["sku_discount_floor_pct"] == 0.75
    assert item["product"]["sku_points_floor_pct"] == 0.75


def test_the_chat_can_link_it_but_is_told_not_to_volunteer_it():
    aliases = json.loads((REPO / "data" / "product-aliases.json").read_text(encoding="utf-8"))
    assert aliases["aliases"][NAME]["url"] == \
        "https://myhealingoasis.com/begin/product/pterostilbene-pure-powder"
    src = (REPO / "app.py").read_text(encoding="utf-8")
    sentence = src[src.index("The pure powders ("):]
    sentence = sentence[:sentence.index("do not volunteer or recommend them")]
    assert NAME in sentence


@pytest.fixture
def appmod(monkeypatch):
    # No reload: the route reads only the catalog, and a reload would leak into later tests.
    import app as a
    monkeypatch.setitem(a.app.config, "TESTING", True)
    return a


def test_the_order_forms_product_list_offers_it_at_forty_dollars(appmod, monkeypatch):
    """Glen adds it from New Order, which reads /api/products?all=1."""
    import dashboard
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "test-secret")
    r = appmod.app.test_client().get("/api/products?all=1",
                                     headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200
    by = {p["slug"]: p for p in r.get_json()["products"]}
    assert by[KEY]["name"] == NAME
    assert by[KEY]["price_cents"] == 4000
