"""Scar Soft Drink cannot be bought anywhere (release 1, 2026-10-09).

Spec: production/05 Formulations/_store-updates/2026-10-09-scar-reduction-program.md,
section 1, approved by Glen. Scar Soft Drink carries `waitlist_only` until
tetrahydrocurcumin is in stock. The one predicate is practitioner_portal.is_orderable,
which also refuses a bundle with a waitlist_only component. An INACTIVE component never
blocks a bundle: the old bundle scar-reduction-program lists the retired msm-syntropy-powder record
and stays on sale at $252 (formulation, 2026-10-09).

Every route below is refused with the fixture product, and for checkout and autoship no
payment provider is called. A refusal names the blocking product.
"""
import json
import os
import sqlite3

import pytest

import app
from dashboard import bundle_pricing
from dashboard import cart_store as CS
from dashboard import client_invoice_lines as cil
from dashboard import practitioner_portal as pp
from dashboard import wholesale_checkout as wc

SCAR = "scar-soft-drink"
# The spec's sentence, verbatim; the page travels as a separate `url` field.
REFUSAL = "Scar Soft Drink is not ready to order yet. Join the waiting list on its page."
URL = "/begin/product/scar-soft-drink"
BUNDLE = "test-scar-core-bundle"


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    """The real Scar Soft Drink record, pinned waitlist_only, plus a bundle holding it, in
    both the app catalog and the wholesale catalog, so $DATA_DIR cannot change either."""
    products = app._PRODUCTS.setdefault("products", {})
    real = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]
    monkeypatch.setitem(products, SCAR, dict(real[SCAR]))
    for s in ("scar-silk", "scar-solve", "vitamin-e-spectrum", "msm-syntropy",
              "msm-syntropy-powder", "scar-reduction-program", "brain-boost"):
        if s in real:
            monkeypatch.setitem(products, s, dict(real[s]))
    monkeypatch.setitem(products, BUNDLE, {
        "name": "Test Scar Core", "bundle": True, "price_cents": 18900,
        "autoship_eligible": True,
        "bundle_component_slugs": [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}]})
    monkeypatch.setattr(pp.pricing, "_load_catalog", lambda: products)
    return products


def _no_payment(monkeypatch):
    calls = []
    for name in ("_stripe_checkout_url_for_reorder", "_stripe_checkout_url_for_order",
                 "_ingest_order"):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, lambda *a, _n=name, **k: calls.append(_n) or "")
    monkeypatch.setattr(app.stripe_pay, "charge_off_session",
                        lambda *a, **k: calls.append("charge_off_session") or {"status": "succeeded"})
    return calls


def _refused(r, code=400):
    assert r.status_code == code, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert REFUSAL in (body.get("error") or ""), body
    assert body.get("url") == URL, body


# ── The predicate ─────────────────────────────────────────────────────────────

def test_the_product_carries_waitlist_only_in_the_catalog_file():
    real = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]
    assert real[SCAR]["waitlist_only"] is True


def test_the_predicate_refuses_it_and_names_it(catalog):
    assert pp.is_orderable(SCAR, catalog) is False
    assert pp.waitlist_block(SCAR, catalog) == {"slug": SCAR, "name": "Scar Soft Drink"}
    msg = pp.waitlist_refusal(pp.waitlist_block(SCAR, catalog))
    assert msg == REFUSAL and msg.url == URL


def test_a_bundle_names_its_waitlist_only_component(catalog):
    assert pp.is_orderable(BUNDLE, catalog) is False
    assert pp.waitlist_block(BUNDLE, catalog)["slug"] == SCAR


def test_a_bundle_inside_a_bundle_is_refused(catalog, monkeypatch):
    """Review round 2: outer -> inner -> scar-soft-drink was orderable."""
    monkeypatch.setitem(catalog, "test-outer", {
        "name": "Outer", "bundle": True, "price_cents": 30000,
        "bundle_component_slugs": [{"slug": "scar-solve", "qty": 1}, {"slug": BUNDLE, "qty": 1}]})
    assert pp.is_orderable("test-outer", catalog) is False
    assert pp.waitlist_block("test-outer", catalog)["slug"] == SCAR


def test_a_bundle_cycle_does_not_loop(catalog, monkeypatch):
    monkeypatch.setitem(catalog, "test-a", {"name": "A", "bundle": True,
                                            "bundle_component_slugs": [{"slug": "test-b"}]})
    monkeypatch.setitem(catalog, "test-b", {"name": "B", "bundle": True,
                                            "bundle_component_slugs": [{"slug": "test-a"}]})
    assert pp.waitlist_block("test-a", catalog) is None


@pytest.mark.parametrize("component", [
    {"name": "Old Scar", "inactive": True, "waitlist_only": True},
    {"name": "Old Scar", "inactive": True, "superseded_by": SCAR},
])
def test_an_inactive_component_never_blocks_its_bundle(catalog, monkeypatch, component):
    """Review round 2, per formulation: only ACTIVE components are checked."""
    monkeypatch.setitem(catalog, "test-old-scar", component)
    monkeypatch.setitem(catalog, "test-bundle-inactive", {
        "name": "B", "bundle": True, "price_cents": 10000,
        "bundle_component_slugs": [{"slug": "scar-silk"}, {"slug": "test-old-scar"}]})
    assert pp.waitlist_block("test-bundle-inactive", catalog) is None
    assert pp.is_orderable("test-bundle-inactive", catalog) is True


def test_the_old_bundle_with_an_inactive_component_stays_on_sale(catalog):
    old = catalog["scar-reduction-program"]
    assert {"slug": "msm-syntropy-powder", "qty": 1} in old["bundle_component_slugs"]
    assert catalog["msm-syntropy-powder"].get("inactive") is True
    assert pp.is_orderable("scar-reduction-program", catalog) is True
    assert pp.waitlist_block("scar-reduction-program", catalog) is None
    assert bundle_pricing.compute_bundle_price_cents(old, catalog) == 25200
    assert old["price_cents"] == 25200


def test_the_old_bundle_page_data_is_unchanged():
    d = app.app.test_client().get("/begin/product-data/scar-reduction-program").get_json()
    assert d["price_cents"] == 25200 and "waitlist_only" not in d
    # Glen, 2026-10-09: the program is the "Scar Support Program"; the slug stays.
    assert d["name"] == "Scar Support Program" and d["slug"] == "scar-reduction-program"


def test_ordinary_products_are_unaffected(catalog):
    assert pp.is_orderable("scar-silk", catalog) is True


# ── Shared choke points ───────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", [SCAR, BUNDLE])
def test_price_cart_refuses_before_pricing(slug):
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": "scar-silk", "qty": 1}, {"slug": slug, "qty": 1}],
                        ship={"country": "US"})
    assert "Scar Soft Drink is not ready to order yet" in str(e.value)


def test_price_cart_refuses_a_zero_qty_line_it_would_price_as_one():
    with pytest.raises(app.CheckoutError):
        app._price_cart([{"slug": SCAR, "qty": 0}], ship={"country": "US"})


def test_shipping_on_orders_already_placed_is_still_quoted(monkeypatch):
    """Review round 2: a known nonzero charge, and every placed line stays in the quote."""
    monkeypatch.setattr(app, "_shipping_for_cart", lambda box_counts, total_bottles: 1300)
    pc = app._price_cart([{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 2}],
                         ship={"country": "US"}, allow_waitlist=True)
    assert pc["shipping_cents"] == 1300
    assert [(l["slug"], l["qty"]) for l in pc["items_rec"]] == [("scar-silk", 1), (SCAR, 2)]


def test_build_order_refuses_before_redeeming_credit(monkeypatch, catalog):
    redeemed = []
    monkeypatch.setattr(wc.wallet, "redeem_for_order", lambda *a, **k: redeemed.append(a) or 0)
    out = wc.build_order([{"slug": SCAR, "qty": 1}], {"id": "p1", "modules_completed": 1},
                         catalog=catalog)
    assert out["ok"] is False and out["error"] == REFUSAL and out["url"] == URL
    assert redeemed == []


def test_invoice_rebuild_refuses_a_posted_line_whole():
    with pytest.raises(cil.NotOrderable):
        cil.rebuild([{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 2}], {},
                    known=lambda s: True, refusal=app._waitlist_refusal)
    # A zero-qty reference line orders nothing, so it is not refused.
    assert cil.rebuild([{"slug": SCAR, "qty": 0}], {}, known=lambda s: True,
                       refusal=app._waitlist_refusal) == [
        {"slug": SCAR, "qty": 0, "format": "bottle"}]


# ── Funnel and cart routes ────────────────────────────────────────────────────

@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.fixture()
def logdb(monkeypatch, tmp_path):
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    cx.close()
    return path


def test_buy_page_sends_the_buyer_to_the_product_page(client):
    r = client.get(f"/begin/buy/{SCAR}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/begin/product/{SCAR}")


def test_begin_checkout_is_refused(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    _refused(client.post(f"/begin/checkout/{SCAR}", json={"email": "a@x.com", "method": "card"}))
    assert calls == []


@pytest.mark.parametrize("slug", [SCAR, BUNDLE])
def test_cart_add_is_refused(client, logdb, slug):
    _refused(client.post("/api/cart/add", json={"slug": slug}))


def test_cart_set_qty_is_refused(client, logdb):
    _refused(client.post("/api/cart/set-qty", json={"slug": SCAR, "qty": 2}))


def test_cart_checkout_refuses_a_mixed_cart_whole(client, logdb, monkeypatch):
    calls = _no_payment(monkeypatch)
    seen = []
    monkeypatch.setattr(app, "_checkout_cart", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    monkeypatch.setattr(app, "_cart_email", lambda: "a@x.com")
    with sqlite3.connect(logdb) as cx:
        token = CS.get_or_create(cx, "tok1", email="a@x.com")
        CS.add_item(cx, token, "scar-silk", qty=1)
        CS.add_item(cx, token, SCAR, qty=1)
    client.set_cookie(app._CART_COOKIE, token)
    _refused(client.post("/api/cart/checkout", json={"address": {"street": "1 A St"}}))
    assert seen == [] and calls == []


def test_cart_shows_it_unavailable(client, logdb):
    with sqlite3.connect(logdb) as cx:
        token = CS.get_or_create(cx, "tok2")
        CS.add_item(cx, token, SCAR, qty=1)
    client.set_cookie(app._CART_COOKIE, token)
    items = client.get("/api/cart").get_json()["items"]
    assert items[0]["available"] is False


def test_reorder_checkout_is_refused(client, monkeypatch, logdb):
    calls = _no_payment(monkeypatch)
    monkeypatch.setattr(app, "_reorder_email_from_cookie", lambda: "a@x.com")
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    _refused(client.post("/reorder/checkout", json={
        "items": [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}],
        "address": {"street": "1 A St", "state": "HI", "zip": "96720", "country": "US"}}))
    assert calls == []


def test_reorder_subscribe_is_refused(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    monkeypatch.setattr(app, "_reorder_email_from_cookie", lambda: "a@x.com")
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    monkeypatch.setattr(app, "_subscriptions_enabled", lambda: True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    monkeypatch.setenv("PRICING_ENGINE_CHECKOUT", "1")
    _refused(client.post("/reorder/subscribe", json={
        "cadence_months": 1, "items": [{"slug": BUNDLE, "qty": 1}],
        "address": {"street": "1 A St", "country": "US"}}))
    assert calls == []


def test_the_autoship_charge_job_refuses_and_keeps_the_date(monkeypatch, tmp_path, capsys):
    """A normal autoship in the same run is charged (the control); the Scar one fails for
    the waitlist reason, is not charged, and keeps its date (review round 2)."""
    from dashboard import subscriptions as subs
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    monkeypatch.setattr(app, "_subscriptions_enabled", lambda: True)
    monkeypatch.setattr(app, "_ingest_order", lambda **k: None)
    monkeypatch.setattr(app, "_send_subscription_email", lambda *a, **k: ("smtp", None))
    monkeypatch.setattr(app, "_active_membership_for_email", lambda email: None)
    monkeypatch.setattr(app, "_is_paid_member", lambda email: False)
    charged = []
    monkeypatch.setattr(app.stripe_pay, "charge_off_session",
                        lambda cus, pm, cents, **k: charged.append(k.get("metadata", {}).get("sub"))
                        or {"status": "succeeded", "id": "ch_test"})
    with sqlite3.connect(path) as cx:
        cx.row_factory = sqlite3.Row
        subs.init_subscriptions_table(cx)
        subs.migrate_add_failed_count(cx)
        ids = {}
        for key, items in (("control", [{"slug": "scar-silk", "qty": 1}]),
                           ("scar", [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}])):
            ids[key] = subs.create(cx, email=f"{key}@x.com", stripe_customer_id="cus_t",
                                   stripe_payment_method_id="pm_t", items=items,
                                   cadence_months=1, ship_address={"state": "CA", "country": "US"},
                                   next_charge_date="2000-01-01")
        cx.commit()
    r = app.app.test_client().post("/api/cron/charge-subscriptions",
                                   headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    out = capsys.readouterr().out
    assert charged == [str(ids["control"])], (r.get_json(), out[-2500:])   # the control
    assert f"price_cart sub={ids['scar']}" in out and REFUSAL in out      # the reason
    with sqlite3.connect(path) as cx:
        dates = dict(cx.execute("SELECT id, next_charge_date FROM subscriptions").fetchall())
    assert dates[ids["scar"]] == "2000-01-01"
    assert dates[ids["control"]] != "2000-01-01"


# ── Client portal and invoice routes ──────────────────────────────────────────

def _portal(monkeypatch, email="pc@x.com"):
    monkeypatch.setattr(app, "_portal_record_for",
                        lambda cx, token: {"email": email, "name": "PC", "content": {}})


def test_portal_order_add_is_refused(client, logdb, monkeypatch):
    _portal(monkeypatch)
    _refused(client.post("/api/portal/tok/order-add", json={"slug": SCAR}))


def test_portal_checkout_is_refused(client, logdb, monkeypatch):
    _portal(monkeypatch)
    calls = _no_payment(monkeypatch)
    monkeypatch.setattr(app, "_portal_entitled_slugs", lambda email: {SCAR, "scar-silk"})
    _refused(client.post("/api/portal/tok/checkout",
                         json={"items": [{"slug": SCAR, "qty": 1}], "method": "card"}))
    assert calls == []


def test_portal_set_format_is_refused(client, logdb, monkeypatch):
    _portal(monkeypatch)
    _refused(client.post("/api/portal/tok/cart/set-format",
                         json={"slug": SCAR, "format": "bottle"}))


def test_client_invoice_update_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_invoice_order_for_token", lambda token: {
        "id": 1, "status": "proposed", "items": [{"slug": "scar-silk", "qty": 1}]})
    reprice = []
    monkeypatch.setattr(app, "_reprice_and_persist_invoice",
                        lambda *a, **k: reprice.append(a) or (None, False))
    _refused(client.post("/api/invoice/tok/update", json={
        "lines": [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}]}))
    assert reprice == []


def test_dispensary_client_checkout_is_refused(client, monkeypatch):
    monkeypatch.setattr(app._pp, "practitioner_id_by_dispensary_code", lambda code: "p1")
    monkeypatch.setattr(app._pp, "portal_data", lambda pid: {"modules_completed": 1})
    built = []
    monkeypatch.setattr(app._dropship, "build_client_order", lambda *a, **k: built.append(a))
    _refused(client.post("/api/client/CODE/checkout", json={
        "email": "pt@x.com", "items": [{"slug": SCAR, "qty": 1}]}))
    assert built == []


# ── Programs and recommendations: listed, never invoiced ──────────────────────

def test_a_program_invoice_gains_no_scar_soft_drink_line(monkeypatch, tmp_path):
    from dashboard import client_portal as cp
    from dashboard import condition_programs as prog
    from dashboard import orders as ord_mod
    from dashboard import client_prices as cprices
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setenv("SUPPORT_PROGRAMS_ENABLED", "1")
    items = [{"slug": "scar-silk", "name": "Scar Silk"},
             {"slug": "scar-solve", "name": "Scar Solve"},
             {"slug": SCAR, "name": "Scar Soft (when available)"},
             {"slug": "ocuheal-eye-drops", "name": "OcuHeal Eye Drops"}]
    with sqlite3.connect(path) as cx:
        cx.row_factory = sqlite3.Row
        cp.init_client_portal_table(cx)
        ord_mod.init_orders_table(cx)
        cprices.init_table(cx)
        prog.init_table(cx)
        prog.upsert(cx, "macular-pucker", "Macular Pucker (Epiretinal Membrane)", False, items)
        token, _ = cp.upsert_portal(cx, "mp@x.com", "MP", {})
    app._migrate_orders_portal_published()
    monkeypatch.setattr(app, "_support_program_for", lambda email: {
        "condition_key": "macular-pucker", "label": "Macular Pucker", "items": items})
    r = app.app.test_client().post(f"/api/portal/{token}/support-program/add-to-invoice")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    with sqlite3.connect(path) as cx:
        lines = json.loads(cx.execute("SELECT items_json FROM orders").fetchone()[0])
    assert [l["slug"] for l in lines] == ["scar-silk", "scar-solve", "ocuheal-eye-drops"]
    assert len(lines) == 3


# ── Practitioner and wholesale routes ─────────────────────────────────────────

def test_practitioner_cart_add_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_practitioner_session_pid", lambda: "p1")
    _refused(client.post("/api/practitioner/cart", json={"slug": SCAR, "qty": 1}))


def _prac(monkeypatch, cart):
    monkeypatch.setattr(app, "_practitioner_session_pid", lambda: "p1")
    monkeypatch.setattr(app._pp, "portal_data", lambda pid: {
        "wholesale_unlocked": True, "cart": cart, "email": "doc@x.com", "name": "Doc",
        "modules_completed": 1})
    monkeypatch.setattr(app._pp, "cart_clear", lambda pid: None)


@pytest.mark.parametrize("route", ["/api/practitioner/checkout",
                                   "/api/practitioner/personal/checkout"])
def test_practitioner_checkouts_are_refused(client, monkeypatch, route):
    _prac(monkeypatch, [{"slug": SCAR, "qty": 1}])
    redeemed = []
    monkeypatch.setattr(wc.wallet, "redeem_for_order", lambda *a, **k: redeemed.append(a) or 0)
    _refused(client.post(route, json={"method": "zelle"}), code=422)
    assert redeemed == []


def test_practitioner_dropship_checkout_is_refused(client, monkeypatch):
    _prac(monkeypatch, [{"slug": BUNDLE, "qty": 1}])
    built = []
    monkeypatch.setattr(app._dropship, "build_dropship_order", lambda *a, **k: built.append(a))
    _refused(client.post("/api/practitioner/dropship/checkout", json={
        "method": "card", "patient_address": {"name": "Pat", "street": "1 A St",
                                              "city": "Hilo", "state": "HI", "zip": "96720"}}))
    assert built == []


# ── Owner and console routes ──────────────────────────────────────────────────

class _Owner:
    role = app._bos_rbac.OWNER
    email = "owner@x.com"


def test_manual_order_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_bos_actor", lambda: _Owner())
    _refused(client.post("/api/orders/manual", json={
        "customer": {"email": "m@x.com"}, "lines": [{"slug": SCAR, "qty": 1}]}))


def test_post_orders_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_bos_actor", lambda: _Owner())
    _refused(client.post("/api/orders", json={"items": [{"slug": SCAR, "qty": 1}]}))


def test_order_edit_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_bos_actor", lambda: _Owner())
    _refused(client.post("/api/orders/1/edit", json={"lines": [{"slug": SCAR, "qty": 1}]}))


def test_console_dropship_create_is_refused(client, monkeypatch):
    monkeypatch.setattr(app, "_console_key_ok", lambda: True)
    monkeypatch.setattr(app._pp, "find_practitioner_id_by_email", lambda e: "p1")
    monkeypatch.setattr(app._pp, "portal_data", lambda pid: {"email": "doc@x.com"})
    _refused(client.post("/api/console/dropship/create", json={
        "practitioner_email": "doc@x.com", "items": [{"slug": SCAR, "qty": 1}],
        "patient_address": {"name": "Pat", "street": "1 A St", "city": "Hilo",
                            "state": "HI", "zip": "96720"}}))


def test_console_dropship_reissue_is_refused_before_any_order_changes(client, monkeypatch):
    monkeypatch.setattr(app, "_console_key_ok", lambda: True)
    monkeypatch.setattr(app._bos_orders, "get_order", lambda cx, oid: {
        "source": "dropship", "items": [{"slug": SCAR, "qty": 1}]})
    expired = []
    monkeypatch.setattr(app.stripe_pay, "expire_open_sessions_for_invoice",
                        lambda ref: expired.append(ref))
    _refused(client.post("/api/console/dropship/reissue",
                         json={"practitioner_id": "11111111-1111-1111-1111-111111111111",
                               "order_ids": [1]}))
    assert expired == []


def _biofield(monkeypatch):
    monkeypatch.setattr(app, "BIOFIELD_CART_ENABLED", True)
    monkeypatch.setattr(app, "_biofield_verify_token", lambda th: (True, {"email": "b@x.com"}))
    monkeypatch.setattr(app, "_biofield_visible_slugs", lambda row, email: [SCAR, "scar-silk"])
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)


def test_biofield_matched_set_orders_without_the_scar_line(client, monkeypatch):
    """Review round 1: a matched-set line not ready to order is skipped and named, and
    never blocks the rest of the order."""
    _biofield(monkeypatch)
    seen = []
    monkeypatch.setattr(app, "_checkout_cart",
                        lambda email, items, **k: seen.append(items) or
                        {"out": {"invoice_id": "r1"}, "stripe_url": "https://pay/x"})
    r = client.post("/begin/biofield/tok/order-checkout", json={
        "items": [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}]})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert [i["slug"] for i in seen[0]] == ["scar-silk"] and len(seen[0]) == 1
    assert r.get_json()["not_ready"] == [{"name": "Scar Soft Drink", "url": URL}]


def test_biofield_preview_names_the_skipped_line(client, monkeypatch):
    _biofield(monkeypatch)
    p = client.post("/begin/biofield/tok/order-preview", json={
        "items": [{"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}]}).get_json()
    assert [l["slug"] for l in p["lines"]] == ["scar-silk"]
    assert p["not_ready"] == [{"name": "Scar Soft Drink", "url": URL}]


# ── Review round 1 (2026-10-09) ───────────────────────────────────────────────

def test_concierge_add_is_refused_before_the_order_changes(client, monkeypatch):
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    found = []
    monkeypatch.setattr(app._bos_orders, "find_order_by_external_ref",
                        lambda cx, ref: found.append(ref))
    monkeypatch.setattr(app._bos_orders, "set_order_qbo_lines",
                        lambda *a, **k: found.append("write"))
    _refused(client.post("/begin/concierge/add", json={
        "slug": SCAR, "invoice_id": "ref1", "email": "c@x.com"}))
    assert found == []


def test_portal_order_my_remedies_goes_through_without_the_scar_line(client, logdb, monkeypatch):
    """A Macular Pucker client's curated list holds Scar Soft Drink. The order goes
    through without it, and the page gets its name and link."""
    monkeypatch.setattr(app, "_portal_record_for", lambda cx, token: {
        "email": "mp@x.com", "name": "MP", "content": {"reorder_items": [
            {"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1},
            {"slug": "scar-solve", "qty": 1}]}})
    monkeypatch.setattr(app, "_merge_accepted_recommendation_items",
                        lambda cx, email, base: list(base))
    priced = []
    real = app._portal_priced_lines
    monkeypatch.setattr(app, "_portal_priced_lines",
                        lambda items, email=None: priced.append(list(items)) or real(items, email=email))
    monkeypatch.setattr(app, "_stripe_checkout_url_for_reorder", lambda *a, **k: "https://pay/x")
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    r = client.post("/api/portal/tok/checkout", json={"method": "card"})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True, body
    assert [i["slug"] for i in priced[0]] == ["scar-silk", "scar-solve"]
    assert len(priced[0]) == 2
    assert body["not_ready"] == [{"name": "Scar Soft Drink", "url": URL}]


def test_portal_cart_is_never_seeded_with_a_not_ready_recommendation(logdb, monkeypatch):
    portal = {"email": "seed@x.com", "content": {"reorder_items": [
        {"slug": "scar-silk", "qty": 1}, {"slug": SCAR, "qty": 1}]}}
    monkeypatch.setattr(app, "_merge_accepted_recommendation_items",
                        lambda cx, email, base: list(base))
    with app.app.test_request_context("/"):
        with sqlite3.connect(logdb) as cx:
            token = app._portal_open_cart(cx, portal, seed=True)
            slugs = [r["slug"] for r in CS.items(cx, token)]
    assert slugs == ["scar-silk"]


def test_the_real_product_data_route_offers_no_way_to_buy():
    d = app.app.test_client().get(f"/begin/product-data/{SCAR}").get_json()
    assert d["waitlist_only"] is True
    assert d["price"] == "" and d["price_cents"] is None and d["regular"] == ""
    for k in ("qty_pricing", "formats", "autoship", "cta_url"):
        assert not d.get(k), k
    assert d["autoship_eligible"] is False
