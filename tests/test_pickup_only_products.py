"""Pickup only products are collected in Hilo and never shipped (Glen, 2026-11-09).

A product with `pickup_only: true` (the 4 L Alkaline Microwater and Miracule Water) may
be sold only on a pickup order. A shipped cart holding one is refused with a 400 naming
it, and the channel is never switched for the buyer. On a pickup order it goes through
with no shipping. It never enters the box packer, so it cannot drop a shipped cart to the
coarse qty rule the way an unfittable own-box item does.

The fixture product stands in for the real records, so these tests do not depend on the
catalogue change that adds them.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

WATER = "test-pickup-water"
WATER_NAME = "Test Pickup Water (4 L)"
BOTTLE = "test-pickup-mix"
BUNDLE = "test-pickup-bundle"
_CAT = {
    WATER: {"slug": WATER, "name": WATER_NAME, "price_cents": 300,
            "bottle_type": "own-box", "pickup_only": True},
    BOTTLE: {"slug": BOTTLE, "name": "Test Mix", "price_cents": 7000,
             "bottle_type": "Dropper 50 mL"},
    BUNDLE: {"slug": BUNDLE, "name": "Test Water Bundle", "bundle": True,
             "price_cents": 7200,
             "bundle_component_slugs": [{"slug": BOTTLE, "qty": 1}, {"slug": WATER, "qty": 1}]},
}
SHIP = {"name": "T", "street": "1 A St", "city": "Hilo", "state": "HI", "zip": "96720",
        "country": "US"}
IN_HOUSE_REFUSAL = f"{WATER_NAME} is pickup only, in Hilo. Choose pickup or remove it."
CUSTOMER_REFUSAL = (f"{WATER_NAME} is pickup only, in Hilo, and cannot be shipped. "
                    "Remove it to check out.")


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


@pytest.fixture
def env(monkeypatch, tmp_path):
    appmod = _app()
    db = str(tmp_path / "m.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    products = appmod._PRODUCTS.setdefault("products", {})
    for slug, rec in _CAT.items():
        monkeypatch.setitem(products, slug, {k: v for k, v in rec.items() if k != "slug"})
    monkeypatch.setattr(appmod, "_get_product",
                        lambda slug: dict(_CAT[slug]) if slug in _CAT else None)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda *a, **k: False)
    appmod._init_people_table()
    from dashboard import orders as O
    cx = sqlite3.connect(db)
    O.init_orders_table(cx)
    cx.close()
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: type("A", (), {"role": appmod._bos_rbac.OWNER})())
    # Pin the box-fit catalogue to this test's db, so a quote is not another test's state.
    monkeypatch.setattr(appmod._shipping, "_default_db_path", lambda: db)
    with sqlite3.connect(db) as scx:
        appmod._shipping.init_shipping_schema(scx)
    return appmod


@pytest.fixture
def packed(env, monkeypatch):
    """Every box_counts the packer was handed."""
    seen = []
    real = env._shipping_for_cart

    def spy(box_counts, total):
        seen.append(dict(box_counts))
        return real(box_counts, total)

    monkeypatch.setattr(env, "_shipping_for_cart", spy)
    return seen


# ── The helper ────────────────────────────────────────────────────────────────

def test_the_helper_finds_a_pickup_only_line(env):
    assert env._cart_has_pickup_only([{"slug": BOTTLE}, {"slug": WATER, "qty": 2}]) is True
    assert env._cart_has_pickup_only([{"slug": BOTTLE}]) is False
    assert env._cart_has_pickup_only([]) is False


def test_the_helper_finds_one_inside_a_bundle(env):
    assert env._cart_has_pickup_only([{"slug": BUNDLE}]) is True


def test_the_refusal_names_the_item_in_both_wordings(env):
    cart = [{"slug": WATER}]
    assert env._pickup_only_refusal(cart, pickup_offered=True) == IN_HOUSE_REFUSAL
    assert env._pickup_only_refusal(cart, pickup_offered=False) == CUSTOMER_REFUSAL
    assert env._pickup_only_refusal([{"slug": BOTTLE}], pickup_offered=True) is None


# ── _price_cart, the engine every checkout prices through ────────────────────

def test_a_shipped_cart_is_refused_and_names_it(env):
    with pytest.raises(env.CheckoutError) as e:
        env._price_cart([{"slug": BOTTLE}, {"slug": WATER}], ship=SHIP)
    assert str(e.value) == CUSTOMER_REFUSAL


def test_a_bundle_holding_one_is_refused(env):
    with pytest.raises(env.CheckoutError):
        env._price_cart([{"slug": BUNDLE}], ship=SHIP)


def test_pickup_lets_it_through_and_never_packs_it(env, packed):
    r = env._price_cart([{"slug": WATER, "qty": 3}], ship=SHIP, pickup=True)
    assert [ln["slug"] for ln in r["items_rec"]] == [WATER]
    assert r["shipping_cents"] == 0
    assert all("own-box" not in bc for bc in packed)


def test_the_packer_skips_it_beside_a_shipped_bottle(env, packed):
    alone = env._price_cart([{"slug": BOTTLE}], ship=SHIP)
    both = env._price_cart([{"slug": BOTTLE}, {"slug": WATER, "qty": 2}], ship=SHIP,
                           pickup=True)
    assert both["shipping_cents"] == alone["shipping_cents"]
    assert packed[-1] == {"Dropper 50 mL": 1}


def test_a_bundle_packs_only_its_shippable_part(env, packed):
    env._price_cart([{"slug": BUNDLE}], ship=SHIP, pickup=True)
    assert packed[-1] == {"Dropper 50 mL": 1}


def test_a_cart_without_one_is_unchanged(env, packed):
    base = env._price_cart([{"slug": BOTTLE, "qty": 2}], ship=SHIP)
    flagged = env._price_cart([{"slug": BOTTLE, "qty": 2}], ship=SHIP, pickup=True)
    assert base["shipping_cents"] == flagged["shipping_cents"] > 0
    assert packed == [{"Dropper 50 mL": 2}, {"Dropper 50 mL": 2}]


# ── Routes ────────────────────────────────────────────────────────────────────

def _manual(env, *, pickup, lines):
    body = {"customer": {"name": "T", "email": "t@example.com",
                         "address": {"address1": "1 A St", "city": "Hilo", "state": "HI",
                                     "zip": "96720", "country": "US"}},
            "lines": lines, "method": "Zelle", "pickup": pickup}
    return env.app.test_client().post("/api/orders/manual", json=body)


def _stored(env, oid):
    from dashboard import orders as O
    cx = sqlite3.connect(env.LOG_DB)
    cx.row_factory = sqlite3.Row
    try:
        return O.get_order(cx, oid)
    finally:
        cx.close()


def test_the_order_builder_refuses_it_shipped(env):
    r = _manual(env, pickup=False, lines=[{"slug": WATER, "qty": 1}])
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json()["error"] == IN_HOUSE_REFUSAL


def test_the_order_builder_takes_it_as_pickup_with_no_shipping(env):
    r = _manual(env, pickup=True, lines=[{"slug": WATER, "qty": 2}, {"slug": BOTTLE, "qty": 1}])
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _stored(env, r.get_json()["order_id"])
    assert o["channel"] == "pickup"
    assert int(o["shipping_cents"] or 0) == 0


def test_the_order_builder_ships_an_ordinary_cart_as_before(env):
    r = _manual(env, pickup=False, lines=[{"slug": BOTTLE, "qty": 1}])
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _stored(env, r.get_json()["order_id"])
    assert o["channel"] != "pickup" and int(o["shipping_cents"] or 0) > 0


def test_the_shipping_preview_refuses_it_shipped(env):
    r = env.app.test_client().post("/api/orders/shipping-preview",
                                   json={"lines": [{"slug": WATER, "qty": 1}],
                                         "address": SHIP, "pickup": False})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json()["error"] == IN_HOUSE_REFUSAL


def test_the_funnel_checkout_refuses_it_before_any_payment(env, monkeypatch):
    calls = []
    for name in ("_stripe_checkout_url_for_reorder", "_stripe_checkout_url_for_order",
                 "_stripe_checkout_url_for_retail", "_ingest_order"):
        if hasattr(env, name):
            monkeypatch.setattr(env, name, lambda *a, _n=name, **k: calls.append(_n) or "")
    monkeypatch.setattr(env, "is_member", lambda *a, **k: True)
    r = env.app.test_client().post(f"/begin/checkout/{WATER}",
                                   json={"email": "t@example.com", "method": "card",
                                         "address": SHIP})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json()["error"] == CUSTOMER_REFUSAL
    assert calls == []


def test_the_manual_board_order_refuses_it_unless_pickup(env, monkeypatch):
    monkeypatch.setattr(env, "_hold_new_order_and_invite", lambda *a, **k: None)
    c = env.app.test_client()
    items = [{"slug": WATER, "qty": 1, "name": WATER_NAME}]
    r = c.post("/api/orders", json={"items": items, "channel": "retail"})
    assert r.status_code == 400 and r.get_json()["error"] == IN_HOUSE_REFUSAL
    r = c.post("/api/orders", json={"items": items, "channel": "pickup"})
    assert r.status_code == 200, r.get_data(as_text=True)


# ── The store shows it ────────────────────────────────────────────────────────

def test_the_product_page_data_carries_the_line(env):
    data = {}
    env._apply_waitlist_page_data(data, WATER, _CAT[WATER])
    assert data["pickup_only_line"] == "Pickup only, in Hilo"
    plain = {}
    env._apply_waitlist_page_data(plain, BOTTLE, _CAT[BOTTLE])
    assert "pickup_only_line" not in plain


def test_the_product_page_and_cart_render_it():
    static = Path(__file__).resolve().parent.parent / "static"
    page = (static / "begin-product.html").read_text()
    assert 'id="product-pickup-only"' in page and "data.pickup_only_line" in page
    assert "Pickup only, in Hilo" in (static / "begin-cart.html").read_text()
