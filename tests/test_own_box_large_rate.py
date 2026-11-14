"""Own-box products that ship in a USPS Large box charge the LIVE Large rate.

Glen 2026-11-14 (money tab): "Change the fixed shipping rates to large box,
except the cloth which depends on the size ordered." Before this, 20 products
carried a typed flat_shipping_cents of 3200 and so missed the Large rate rise
to $33 (usps_rates id 10, confirmed 3 Nov). Now 19 of them name the box
(flat_shipping_boxes ["L"]) and follow usps_rates. The Bamboo Sanctuary Cloth
packs as filler: no charge beside anything else, one Small box alone.
"""
import sqlite3

import pytest

import app
from dashboard import shipping

US = {"name": "T", "state": "CA", "country": "US"}

LARGE_BOX = [
    "molecular-hydrogen-bottle", "nir-brain-frequency-helmet", "hair-growth-helmet",
    "air-surface-pro-plus", "denas-microcurrent-eye-system",
]
CLOTH = "bamboo-sanctuary-cloth-with-40-silver"


@pytest.fixture
def rates(monkeypatch, tmp_path):
    dbp = str(tmp_path / "chat_log.db")
    with sqlite3.connect(dbp) as cx:
        shipping.init_shipping_schema(cx)
    monkeypatch.setattr(shipping, "_default_db_path", lambda: dbp)

    def set_large(retail_cents):
        rid = shipping.propose_rate_update("L", retail_cents, "test", "2099-01-01", db_path=dbp)
        shipping.confirm_rate_update(rid, "test", db_path=dbp)
        return shipping.get_current_rates(db_path=dbp)["L"]["charged_cents"]
    return set_large


def _large_box_slugs():
    return sorted(s for s, p in app._PRODUCTS["products"].items()
                  if p.get("flat_shipping_boxes") == ["L"] and not p.get("flat_shipping_cents"))


def test_exactly_the_nineteen_name_the_large_box():
    slugs = _large_box_slugs()
    assert len(slugs) == 19, slugs
    for s in LARGE_BOX:
        assert s in slugs, s
    assert CLOTH not in slugs
    for s in slugs:
        assert "flat_shipping_cents" not in app._get_product(s), f"{s}: typed rate left beside the box"


def test_no_typed_large_rate_remains():
    left = sorted(s for s, p in app._PRODUCTS["products"].items()
                  if p.get("flat_shipping_cents") == 3200)
    assert left == []
    cloth = app._get_product(CLOTH)
    assert cloth.get("ships_as_filler") is True
    assert "flat_shipping_cents" not in cloth and "flat_shipping_boxes" not in cloth


def test_large_box_items_charge_the_live_large_rate(rates):
    live = rates(3275)                 # today's prod row: $32.75 retail -> $33 charged
    assert live == 3300
    for s in _large_box_slugs():
        pc = app._price_cart([{"slug": s, "qty": 1}], ship=US, channel="retail")
        assert pc["shipping_cents"] == 3300, s


def test_a_usps_change_carries_through(rates):
    """Mutation of the rate itself: a new confirmed Large rate moves every item."""
    assert rates(3450) == 3500
    for s in _large_box_slugs():
        pc = app._price_cart([{"slug": s, "qty": 2}], ship=US, channel="retail")
        assert pc["shipping_cents"] == 7000, s


def test_cloth_alone_ships_one_small_box(rates):
    """Glen 2026-11-14: "It would fit a small box." Alone: one Small box at the live
    S rate. Longer cuts are unruled, so qty above 1 still charges one Small box."""
    small = shipping.get_current_rates()["S"]["charged_cents"]
    for qty in (1, 3):
        pc = app._price_cart([{"slug": CLOTH, "qty": qty}], ship=US, channel="retail")
        assert pc["shipping_cents"] == small, qty


def test_cloth_with_bottles_adds_nothing(rates):
    """Glen: it fits "as part of the packing material, not take extra space"."""
    bottle = "neuro-magnesium"
    # A behaviour pin: an own-box line with no flat rate already packs at no extra
    # charge here, so the discriminating test is the own-box one below.
    alone = app._price_cart([{"slug": bottle, "qty": 4}], ship=US, channel="retail")
    both = app._price_cart([{"slug": bottle, "qty": 4}, {"slug": CLOTH, "qty": 2}],
                           ship=US, channel="retail")
    assert alone["shipping_cents"] > 0
    assert both["shipping_cents"] == alone["shipping_cents"]


def test_cloth_with_an_own_box_item_adds_nothing(rates):
    rates(3275)
    pc = app._price_cart([{"slug": "molecular-hydrogen-bottle", "qty": 1}, {"slug": CLOTH, "qty": 1}],
                         ship=US, channel="retail")
    assert pc["shipping_cents"] == 3300


def test_cloth_alone_is_still_us_only():
    with pytest.raises(app.CheckoutError):
        app._price_cart([{"slug": CLOTH, "qty": 1}],
                        ship={"name": "T", "country": "AU"}, channel="retail")


def test_cloth_alone_on_pickup_ships_nothing():
    res = app._price_inhouse_invoice([{"slug": CLOTH, "qty": 1}], email="", pickup=True, ship=US)
    assert res["shipping_cents"] == 0


def test_large_box_item_never_reaches_the_packer(rates, monkeypatch):
    rates(3275)
    seen = []
    real = app._shipping.quote
    monkeypatch.setattr(app._shipping, "quote", lambda bc, *a, **k: (seen.append(dict(bc)), real(bc, *a, **k))[1])
    pc = app._price_cart([{"slug": "molecular-hydrogen-bottle", "qty": 1}], ship=US, channel="retail")
    assert pc["shipping_cents"] == 3300
    assert all("own-box" not in bc for bc in seen)


def test_large_box_item_is_still_us_only(rates):
    with pytest.raises(app.CheckoutError):
        app._price_cart([{"slug": "molecular-hydrogen-bottle", "qty": 1}],
                        ship={"name": "T", "country": "AU"}, channel="retail")


def test_rates_table_down_falls_back_to_hard_large(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(app._shipping, "get_current_rates", boom)
    assert app._own_box_flat_cents({"flat_shipping_boxes": ["L"]}) == app._SHIP_FALLBACK_HARD["L"]


def test_unknown_box_size_is_refused_not_free():
    with pytest.raises(app.CheckoutError):
        app._own_box_flat_cents({"name": "X", "flat_shipping_boxes": ["XL"]})


def test_box_and_fixed_amount_add(rates):
    rates(3275)
    assert app._own_box_flat_cents({"flat_shipping_boxes": ["L"], "flat_shipping_cents": 10000}) == 13300


def test_vegicaps_size_4_is_twelve_dollars_and_size_00_unchanged():
    """Glen 2026-11-14: "update the store price to $12 (same item, acid resistant)"."""
    assert app._get_product("empty-vegicaps-size-4")["price_cents"] == 1200
    assert app._get_product("empty-vegicaps-size-00")["price_cents"] == 800


def test_packages_follow_the_live_rates(rates):
    """Glen 2026-11-14: "The packages follow the live rate"."""
    rates(3275)
    small_med = shipping.get_current_rates()
    m = small_med["M"]["charged_cents"]
    pc = app._price_cart([{"slug": "photobiomodulation-package", "qty": 1}], ship=US, channel="retail")
    assert pc["shipping_cents"] == 3300 + m
    pc = app._price_cart([{"slug": "healing-tools-package", "qty": 1}], ship=US, channel="retail")
    assert pc["shipping_cents"] == 3300 + 10000
