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
BUNDLE_NAME = "Test Water Bundle"
RETIRED = "test-pickup-water-old"
OLD_BUNDLE = "test-pickup-old-bundle"
UNKNOWN_BUNDLE = "test-pickup-unknown-bundle"
_CAT = {
    WATER: {"slug": WATER, "name": WATER_NAME, "price_cents": 300,
            "bottle_type": "own-box", "pickup_only": True},
    BOTTLE: {"slug": BOTTLE, "name": "Test Mix", "price_cents": 7000,
             "bottle_type": "Dropper 50 mL"},
    BUNDLE: {"slug": BUNDLE, "name": BUNDLE_NAME, "bundle": True,
             "price_cents": 7200,
             "bundle_component_slugs": [{"slug": BOTTLE, "qty": 1}, {"slug": WATER, "qty": 1}]},
    # A retired record superseded by the water, with a shippable bottle of its own.
    RETIRED: {"slug": RETIRED, "name": "Old Water", "price_cents": 300, "inactive": True,
              "superseded_by": WATER, "bottle_type": "Dropper 50 mL"},
    # One part we can identify (a plain bottle) and one we cannot.
    UNKNOWN_BUNDLE: {"slug": UNKNOWN_BUNDLE, "name": "Mystery Bundle", "bundle": True,
                     "price_cents": 7200,
                     "bundle_component_slugs": [{"slug": BOTTLE, "qty": 1},
                                                {"slug": "no-such-product", "qty": 1}]},
    OLD_BUNDLE: {"slug": OLD_BUNDLE, "name": "Old Water Bundle", "bundle": True,
                 "price_cents": 7200,
                 "bundle_component_slugs": [{"slug": BOTTLE, "qty": 1},
                                            {"slug": RETIRED, "qty": 1}]},
}
SHIP = {"name": "T", "street": "1 A St", "city": "Hilo", "state": "HI", "zip": "96720",
        "country": "US"}
IN_HOUSE_REFUSAL = f"{WATER_NAME} is pickup only, in Hilo. Choose pickup or remove it."
CUSTOMER_LINE = "Pickup only, in Hilo. Call (808) 217-9647 to arrange pickup."
CUSTOMER_REFUSAL = f"{WATER_NAME} cannot be shipped. {CUSTOMER_LINE}"
BUNDLE_IN_HOUSE = (f"{BUNDLE_NAME} contains {WATER_NAME}, which is pickup only, in Hilo. "
                   "Choose pickup or remove it.")
BUNDLE_CUSTOMER = f"{BUNDLE_NAME} contains {WATER_NAME}, which cannot be shipped. {CUSTOMER_LINE}"
CARER, PET = "carer@example.com", "pet@example.com"


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    # No skip on import failure: this guards money, so a broken app must fail CI.
    return importlib.import_module("app")


@pytest.fixture
def env(monkeypatch, tmp_path):
    appmod = _app()
    db = str(tmp_path / "m.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    products = appmod._PRODUCTS.setdefault("products", {})
    for slug, rec in _CAT.items():
        monkeypatch.setitem(products, slug, {k: v for k, v in rec.items() if k != "slug"})
    # The real _get_product reads these, so a retired slug follows superseded_by.
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


def test_a_non_string_slug_is_skipped_not_a_500(env):
    cart = [{"slug": 7}, {"slug": None}, {"slug": ["x"]}, {"slug": WATER}]
    assert env._cart_pickup_only_names(cart) == [WATER_NAME]
    assert env._cart_has_pickup_only([{"slug": 7}]) is False


def test_a_bundle_naming_a_retired_slug_follows_it_to_the_water(env, packed):
    assert env._cart_has_pickup_only([{"slug": OLD_BUNDLE}]) is True
    with pytest.raises(env.CheckoutError):
        env._price_cart([{"slug": OLD_BUNDLE}], ship=SHIP)
    env._price_cart([{"slug": OLD_BUNDLE}], ship=SHIP, pickup=True)
    assert packed[-1] == {"Dropper 50 mL": 1}   # the retired record's bottle is not packed


def test_a_bundle_refusal_names_the_bundle_and_what_it_holds(env):
    cart = [{"slug": BUNDLE}]
    assert env._pickup_only_refusal(cart, pickup_offered=True) == BUNDLE_IN_HOUSE
    assert env._pickup_only_refusal(cart, pickup_offered=False) == BUNDLE_CUSTOMER


def test_the_customer_refusal_uses_the_page_constant(env):
    assert env.PICKUP_ONLY_CUSTOMER_LINE == CUSTOMER_LINE
    assert CUSTOMER_LINE in env._pickup_only_refusal([{"slug": WATER}], pickup_offered=False)


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

def _page(**extra):
    return {"price": "$3.00", "price_cents": 300, "cta_url": "/begin/buy/x",
            "autoship": {"first_pct": 5}, "autoship_eligible": True,
            "sections": [{"id": "cta"}, {"id": "intro"}], **extra}


def test_the_product_page_hides_buy_and_autoship_and_shows_the_line(env):
    data = _page()
    env._apply_waitlist_page_data(data, WATER, _CAT[WATER])
    assert data["pickup_only_line"] == CUSTOMER_LINE
    assert "cta_url" not in data and "autoship" not in data
    assert data["autoship_eligible"] is False
    assert [s["id"] for s in data["sections"]] == ["intro"]
    assert data["price_cents"] == 300          # still priced: paid at pickup
    plain = _page()
    env._apply_waitlist_page_data(plain, BOTTLE, _CAT[BOTTLE])
    assert "pickup_only_line" not in plain and plain["cta_url"] and plain["autoship"]


def test_the_buy_page_sends_the_buyer_back_to_the_product_page(env):
    r = env.app.test_client().get(f"/begin/buy/{WATER}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/begin/product/{WATER}")


def test_the_product_page_and_cart_render_it():
    static = Path(__file__).resolve().parent.parent / "static"
    page = (static / "begin-product.html").read_text()
    assert 'id="product-pickup-only"' in page and "data.pickup_only_line" in page
    assert "!data.pickup_only_line) { renderCartControl(slug); }" in page
    assert "Pickup only, in Hilo" in (static / "begin-cart.html").read_text()


# ── Caregiver billing carries the pickup tick (review, 2026-11-09) ───────────

@pytest.fixture
def carer(env, monkeypatch):
    from dashboard import household as H
    monkeypatch.setattr(env, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    monkeypatch.setattr(env, "_hold_new_order_and_invite", lambda *a, **k: None)
    with sqlite3.connect(env.LOG_DB) as cx:
        H.init_household_tables(cx)
        H.add_member(cx, CARER, PET, label="Pet", relationship="pet")
        H.set_bill_with_caregiver(cx, CARER, PET, True)
        cx.commit()
    return env


def _raise(env, email, lines, **extra):
    return env.app.test_client().post(
        "/api/orders/manual",
        json={"customer": {"email": email, "name": "N"}, "lines": lines, **extra})


def test_a_new_caregiver_invoice_opens_as_pickup_when_ticked(carer):
    r = _raise(carer, PET, [{"slug": WATER, "qty": 1}], pickup=True)
    body = r.get_json()
    assert r.status_code == 200 and body["billed_to_caregiver"], body
    o = _stored(carer, body["order_id"])
    assert o["email"] == CARER and o["channel"] == "pickup"
    assert int(o["shipping_cents"] or 0) == 0


def test_an_open_caregiver_invoice_becomes_pickup_when_ticked(carer):
    first = _raise(carer, CARER, [{"slug": BOTTLE, "qty": 1}], pickup=False).get_json()
    assert _stored(carer, first["order_id"])["channel"] != "pickup"
    r = _raise(carer, PET, [{"slug": WATER, "qty": 1}], pickup=True)
    body = r.get_json()
    assert r.status_code == 200 and body["order_id"] == first["order_id"], body
    o = _stored(carer, first["order_id"])
    assert o["channel"] == "pickup" and int(o["shipping_cents"] or 0) == 0


def test_a_caregiver_invoice_still_refuses_it_unticked(carer):
    r = _raise(carer, PET, [{"slug": WATER, "qty": 1}], pickup=False)
    assert r.status_code == 400, r.get_data(as_text=True)


def test_a_biofield_re_raise_takes_the_explicit_pickup(env):
    first = _raise(env, "re@example.com", [{"slug": BOTTLE, "qty": 1}], pickup=False).get_json()
    r = _raise(env, "re@example.com", [{"slug": WATER, "qty": 1}],
               update_order_id=first["order_id"], pickup=True)
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _stored(env, first["order_id"])
    assert o["channel"] == "pickup" and int(o["shipping_cents"] or 0) == 0
    # With no pickup key the order keeps its own channel: still refused when it ships.
    second = _raise(env, "re2@example.com", [{"slug": BOTTLE, "qty": 1}], pickup=False).get_json()
    r = _raise(env, "re2@example.com", [{"slug": WATER, "qty": 1}],
               update_order_id=second["order_id"])
    assert r.status_code == 400


# ── The autoship charge job (review, 2026-11-09) ──────────────────────────────

def test_the_charge_job_counts_a_pickup_only_failure_and_warns(env, monkeypatch, caplog):
    from dashboard import subscriptions as subs
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    monkeypatch.setattr(env, "_subscriptions_enabled", lambda: True)
    monkeypatch.setattr(env, "_ingest_order", lambda **k: None)
    emails = []
    monkeypatch.setattr(env, "_send_subscription_email",
                        lambda to, kind, data: emails.append((to, kind)) or ("smtp", None))
    monkeypatch.setattr(env, "_active_membership_for_email", lambda email: None)
    charged = []
    monkeypatch.setattr(env.stripe_pay, "charge_off_session",
                        lambda *a, **k: charged.append(1) or {"status": "succeeded", "id": "ch"})
    with sqlite3.connect(env.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        subs.init_subscriptions_table(cx)
        subs.migrate_add_failed_count(cx)
        sid = subs.create(cx, email="w@example.com", stripe_customer_id="cus_t",
                          stripe_payment_method_id="pm_t", items=[{"slug": WATER, "qty": 1}],
                          cadence_months=1, ship_address={"state": "HI", "country": "US"},
                          next_charge_date="2000-01-01")
        cx.commit()
    with caplog.at_level("WARNING"):
        r = env.app.test_client().post("/api/cron/charge-subscriptions",
                                       headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200 and r.get_json()["failed"] == 1, r.get_data(as_text=True)
    assert charged == []
    assert not any(kind == "payment_failed" for _to, kind in emails)
    assert any(f"sub={sid}" in rec.getMessage() and rec.levelname == "WARNING"
               for rec in caplog.records)
    with sqlite3.connect(env.LOG_DB) as cx:
        n, date = cx.execute("SELECT failed_count, next_charge_date FROM subscriptions "
                             "WHERE id=?", (sid,)).fetchone()
    assert n == 1 and date == "2000-01-01"


def _member_order(env, email, lines, *, pickup):
    """An order of the member's own, not redirected to the caregiver."""
    r = env.app.test_client().post(
        "/api/orders/manual",
        json={"customer": {"email": email, "name": "N"}, "lines": lines,
              "pickup": pickup, "_caregiver_route": False})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["order_id"]


def test_the_board_move_refuses_a_channel_mismatch_and_switches_nothing(carer):
    target = _raise(carer, CARER, [{"slug": BOTTLE, "qty": 1}], pickup=False).get_json()
    before = _stored(carer, target["order_id"])
    pet = _member_order(carer, PET, [{"slug": WATER, "qty": 1}], pickup=True)
    r = carer.app.test_client().post(f"/api/orders/{pet}/bill-with-caregiver", json={})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert "Make them match first" in r.get_json()["error"]
    after = _stored(carer, target["order_id"])
    assert after["channel"] == before["channel"] != "pickup"
    assert after["shipping_cents"] == before["shipping_cents"]
    assert _stored(carer, pet)["status"] != "cancelled"


def test_the_board_move_refuses_a_shipped_order_onto_a_pickup_invoice(carer):
    _raise(carer, CARER, [{"slug": WATER, "qty": 1}], pickup=True)
    pet = _member_order(carer, PET, [{"slug": BOTTLE, "qty": 1}], pickup=False)
    r = carer.app.test_client().post(f"/api/orders/{pet}/bill-with-caregiver", json={})
    assert r.status_code == 400, r.get_data(as_text=True)


def test_the_board_move_onto_a_matching_pickup_invoice_goes_through(carer):
    target = _raise(carer, CARER, [{"slug": WATER, "qty": 1}], pickup=True).get_json()
    pet = _member_order(carer, PET, [{"slug": WATER, "qty": 2}], pickup=True)
    r = carer.app.test_client().post(f"/api/orders/{pet}/bill-with-caregiver", json={})
    assert r.status_code == 200 and r.get_json()["order_id"] == target["order_id"]
    assert _stored(carer, target["order_id"])["channel"] == "pickup"


def test_the_board_move_opens_a_new_pickup_invoice_for_a_pickup_order(carer):
    pet = _member_order(carer, PET, [{"slug": WATER, "qty": 1}], pickup=True)
    r = carer.app.test_client().post(f"/api/orders/{pet}/bill-with-caregiver", json={})
    body = r.get_json()
    assert r.status_code == 200 and body["created"] is True, body
    o = _stored(carer, body["order_id"])
    assert o["email"] == CARER and o["channel"] == "pickup"


# ── A bundle with a part we cannot identify fails closed (review, 2026-11-09) ─

def test_an_unknown_bundle_part_counts_as_possibly_pickup_only(env):
    cart = [{"slug": UNKNOWN_BUNDLE}]
    assert env._cart_has_pickup_only(cart) is True
    assert env._cart_pickup_only_names(cart) == []
    assert "cannot be identified" in env._pickup_only_refusal(cart, pickup_offered=True)
    assert "(808) 217-9647" in env._pickup_only_refusal(cart, pickup_offered=False)


def test_the_parts_that_resolve_are_still_walked(env, monkeypatch):
    bundle = dict(_CAT[UNKNOWN_BUNDLE], bundle_component_slugs=[
        {"slug": "no-such-product", "qty": 1}, {"slug": WATER, "qty": 1}])
    monkeypatch.setitem(env._PRODUCTS["products"], UNKNOWN_BUNDLE,
                        {k: v for k, v in bundle.items() if k != "slug"})
    hits = env._cart_pickup_only_hits([{"slug": UNKNOWN_BUNDLE}])
    assert hits == [("Mystery Bundle", WATER_NAME), ("Mystery Bundle", None)]


def test_the_board_create_gate_refuses_an_unknown_bundle_shipped(env, monkeypatch):
    monkeypatch.setattr(env, "_hold_new_order_and_invite", lambda *a, **k: None)
    c = env.app.test_client()
    items = [{"slug": UNKNOWN_BUNDLE, "qty": 1, "name": "Mystery Bundle"}]
    r = c.post("/api/orders", json={"items": items, "channel": "retail"})
    assert r.status_code == 400 and "cannot be identified" in r.get_json()["error"]
    r = c.post("/api/orders", json={"items": items, "channel": "pickup"})
    assert r.status_code == 200, r.get_data(as_text=True)
