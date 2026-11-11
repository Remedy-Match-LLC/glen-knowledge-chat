"""Alkalize Bicarbonate Blend is orderable again (2026-11-10).

Glen, 2026-10-20: Alkalize was out of stock and being reformulated. "You can keep the link.
Take back orders, or a waiting list." It carried `waitlist_only`, the same flag as Scar
Soft Drink and Fungal Nail Repair, so the one predicate (practitioner_portal.is_orderable)
refused it at every cart, checkout, invoice and autoship path.

Glen, 2026-11-10, platform tab, asked whether Alkalize is ready to order again so it comes
off the waiting list: "yes I found the inventory". The flag is gone. Every path that refused
it now accepts it. The slug and page stay. Its waiting-list wording (TEXTS) stays, because
its sign-ups are kept, but the list is closed to new sign-ups.

The generic waiting-list tests that used Alkalize as the example now use Fungal Nail Repair,
which is still `waitlist_only`.

Each test reads the REAL record from data/products.json, so putting the flag back fails here.
"""
import json
import os
import sqlite3

import pytest

import app
from dashboard import cart_store as CS
from dashboard import practitioner_portal as pp
from dashboard import wholesale_checkout as wc

SLUG = "alkalize-bicarbonate-blend"
CONTROL = "magnesium-syntropy"
REFUSAL = ("Alkalize Bicarbonate Blend is not ready to order yet. "
           "Join the waiting list on its page.")
URL = f"/begin/product/{SLUG}"
# Still waitlist_only, so the generic sign-up tests run against a live list.
HELD = "fungal-nail-repair"
HELD_REFUSAL = ("Fungal Nail Repair is not ready to order yet. "
                "Join the waiting list on its page.")
HELD_URL = f"/begin/product/{HELD}"
UNIT_CENTS = 7000          # Alkalize's retail price, a unit


def _real():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    """The real records, copied into app's catalog and the pricing catalog, so a test
    that replaced app's products earlier in the run cannot change the answer."""
    products = app._PRODUCTS.setdefault("products", {})
    real = _real()
    for s in (SLUG, CONTROL, HELD):
        monkeypatch.setitem(products, s, dict(real[s]))
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


def _refused(r, line=HELD_REFUSAL, url=HELD_URL, code=400):
    assert r.status_code == code, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert line in (body.get("error") or ""), body
    assert body.get("url") == url, body


def _alk_line(pc):
    """Alkalize's one priced line in a _price_cart result."""
    (line,) = [ln for ln in pc["priced"]["lines"] if ln["slug"] == SLUG]
    return line


def _not_refused(r):
    """The answer carries no waiting-list refusal for Alkalize."""
    text = r.get_data(as_text=True)
    assert "not ready to order" not in text and REFUSAL not in text, (r.status_code, text[:300])
    body = r.get_json(silent=True) or {}
    assert not body.get("waitlist") and body.get("url") != URL, body


def _stop_after_pricing(monkeypatch):
    """The real _price_cart runs, so a waiting-list refusal inside it still fires. Once it
    has priced the cart, a CheckoutError stops the request before any payment."""
    priced = []
    real = app._price_cart

    def spy(cart, **k):
        out = real(cart, **k)
        priced.append({"slugs": [line["slug"] for line in cart], "result": out})
        raise app.CheckoutError("stopped after pricing")
    monkeypatch.setattr(app, "_price_cart", spy)
    return priced


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


# ── The record ────────────────────────────────────────────────────────────────

def test_the_record_is_no_longer_waitlist_only_and_keeps_its_page():
    """Glen, 2026-11-10: "yes I found the inventory". No flag blocks it."""
    p = _real()[SLUG]
    assert "waitlist_only" not in p
    assert not p.get("out_of_stock") and not p.get("inactive") and not p.get("enquiry_only")
    assert p["price_cents"] == 7000
    assert p["url"] == "https://myhealingoasis.com/begin/product/alkalize-bicarbonate-blend"


def test_the_predicate_accepts_it_like_the_control(catalog):
    """Glen, 2026-11-10: orderable again. A still-held product is refused, so the
    predicate is live."""
    assert pp.is_orderable(SLUG, catalog) is True
    assert pp.is_orderable(CONTROL, catalog) is True
    assert pp.waitlist_block(SLUG, catalog) is None
    assert pp.is_orderable(HELD, catalog) is False


# ── Cart and checkout ─────────────────────────────────────────────────────────

def test_price_cart_prices_it():
    """Glen, 2026-11-10: orderable again, so the cart is priced, Alkalize included at
    $70 a unit before any volume discount."""
    pc = app._price_cart([{"slug": CONTROL, "qty": 1}, {"slug": SLUG, "qty": 2}],
                         ship={"country": "US"})
    line = _alk_line(pc)
    assert line["qty"] == 2 and line["list_cents"] == 2 * UNIT_CENTS
    assert 0 < line["line_total_cents"] <= line["list_cents"]
    (qbo,) = [q for q in pc["qbo_lines"] if q["name"] == "Alkalize Bicarbonate Blend"]
    assert qbo["amount"] == UNIT_CENTS / 100 and qbo["qty"] == 2


def test_price_cart_refuses_a_held_product_before_pricing():
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": CONTROL, "qty": 1}, {"slug": HELD, "qty": 1}],
                        ship={"country": "US"})
    assert HELD_REFUSAL in str(e.value)


def test_buy_page_sends_a_held_buyer_to_the_product_page(client):
    r = client.get(f"/begin/buy/{HELD}")
    assert r.status_code == 302 and r.headers["Location"].endswith(HELD_URL)


def test_buy_page_serves_the_buy_page(client):
    """Glen, 2026-11-10: the buy page no longer sends the buyer back to the product page."""
    r = client.get(f"/begin/buy/{SLUG}")
    assert r.status_code == 200
    assert not (r.headers.get("Location") or "").endswith(URL)


def test_begin_checkout_reaches_pricing_and_no_payment_here(client, monkeypatch):
    """Glen, 2026-11-10: checkout passes the waiting-list gate and prices the cart. The
    test stops it there, so no payment is attempted."""
    calls = _no_payment(monkeypatch)
    priced = _stop_after_pricing(monkeypatch)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    r = client.post(f"/begin/checkout/{SLUG}",
                    json={"email": "a@x.com", "method": "card", "qty": 2})
    _not_refused(r)
    assert r.status_code == 400 and r.get_json()["error"] == "stopped after pricing"
    assert [p["slugs"] for p in priced] == [[SLUG]] and calls == []
    line = _alk_line(priced[0]["result"])
    assert line["qty"] == 2 and line["list_cents"] == 2 * UNIT_CENTS
    assert line["line_total_cents"] > 0


def test_begin_checkout_refuses_a_held_product_before_any_payment(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    priced = _stop_after_pricing(monkeypatch)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    _refused(client.post(f"/begin/checkout/{HELD}", json={"email": "a@x.com", "method": "card"}))
    assert calls == [] and priced == []


def test_cart_add_accepts_it(client, logdb):
    """Glen, 2026-11-10: the cart takes it, and the line is available."""
    r = client.post("/api/cart/add", json={"slug": SLUG})
    _not_refused(r)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    (item,) = [i for i in r.get_json()["items"] if i["slug"] == SLUG]
    assert item["available"] is True


def test_cart_add_refuses_a_held_product(client, logdb):
    _refused(client.post("/api/cart/add", json={"slug": HELD}))


def _subscribe_ready(monkeypatch):
    monkeypatch.setattr(app, "_reorder_email_from_cookie", lambda: "a@x.com")
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    monkeypatch.setattr(app, "_subscriptions_enabled", lambda: True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    monkeypatch.setenv("PRICING_ENGINE_CHECKOUT", "1")


def test_reorder_subscribe_reaches_pricing(client, monkeypatch):
    """Glen, 2026-11-10: a subscription holding Alkalize is priced, not refused, at $70 a
    unit before discount. The test stops it after pricing, so no payment is attempted."""
    calls = _no_payment(monkeypatch)
    priced = _stop_after_pricing(monkeypatch)
    _subscribe_ready(monkeypatch)
    r = client.post("/reorder/subscribe", json={
        "cadence_months": 1, "items": [{"slug": SLUG, "qty": 2}],
        "address": {"street": "1 A St", "country": "US"}})
    _not_refused(r)
    assert r.status_code == 400 and r.get_json()["error"] == "stopped after pricing"
    assert priced and all(SLUG in p["slugs"] for p in priced) and calls == []
    line = _alk_line(priced[0]["result"])
    assert line["qty"] == 2 and line["list_cents"] == 2 * UNIT_CENTS
    assert line["line_total_cents"] > 0


def test_reorder_subscribe_refuses_a_held_product(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    _subscribe_ready(monkeypatch)
    _refused(client.post("/reorder/subscribe", json={
        "cadence_months": 1, "items": [{"slug": HELD, "qty": 1}],
        "address": {"street": "1 A St", "country": "US"}}))
    assert calls == []


# ── Invoices ──────────────────────────────────────────────────────────────────

def test_wholesale_order_is_built_and_credit_redeemed(monkeypatch, catalog):
    """Glen, 2026-11-10: a practitioner's order holding Alkalize is built. Alkalize has no
    wholesale_discount_pct, so its unit is wholesale_pricing's blended curve, not its
    retail price: one bottle is the q=1 knot, $50 (KNOT_Q1_CENTS)."""
    from dashboard import wholesale_pricing as wp
    assert catalog[SLUG].get("wholesale_discount_pct") is None
    redeemed = []
    monkeypatch.setattr(wc.wallet, "redeem_for_order", lambda *a, **k: redeemed.append(a) or 0)
    out = wc.build_order([{"slug": SLUG, "qty": 1}], {"id": "p1", "modules_completed": 1},
                         catalog=catalog)
    assert out["ok"] is True, out
    assert "url" not in out and REFUSAL not in json.dumps(out, default=str)
    assert len(redeemed) == 1
    assert wp.KNOT_Q1_CENTS == 5000
    (line,) = out["qbo_payload"]["lines"]
    assert line["name"] == "Alkalize Bicarbonate Blend" and line["qty"] == 1
    assert line["amount"] == wp.KNOT_Q1_CENTS / 100
    assert out["subtotal_cents"] == wp.KNOT_Q1_CENTS
    assert redeemed[0][1] == wp.KNOT_Q1_CENTS    # credit is offered against the real subtotal


def test_wholesale_order_refuses_a_held_product_before_credit_is_redeemed(monkeypatch, catalog):
    redeemed = []
    monkeypatch.setattr(wc.wallet, "redeem_for_order", lambda *a, **k: redeemed.append(a) or 0)
    out = wc.build_order([{"slug": HELD, "qty": 1}], {"id": "p1", "modules_completed": 1},
                         catalog=catalog)
    assert out["ok"] is False and out["error"] == HELD_REFUSAL and out["url"] == HELD_URL
    assert redeemed == []


def test_console_biofield_catalog_marks_it_ready(monkeypatch, catalog):
    """Glen, 2026-11-10: the console catalog lists it as orderable, like the control."""
    monkeypatch.setattr(app._bos_products, "catalog", lambda **kw: [
        dict(catalog[s], slug=s) for s in (SLUG, CONTROL)])
    monkeypatch.setattr(app, "_portal_console_ok", lambda: True)
    rows = {p["slug"]: p for p in
            app.app.test_client().get("/api/console/biofield-portal/catalog").get_json()["products"]}
    assert rows[SLUG]["waitlist_only"] is False
    assert rows[CONTROL]["waitlist_only"] is False


def test_console_biofield_catalog_marks_a_held_product_not_ready(monkeypatch, catalog):
    monkeypatch.setattr(app._bos_products, "catalog", lambda **kw: [
        dict(catalog[s], slug=s) for s in (HELD, CONTROL)])
    monkeypatch.setattr(app, "_portal_console_ok", lambda: True)
    rows = {p["slug"]: p for p in
            app.app.test_client().get("/api/console/biofield-portal/catalog").get_json()["products"]}
    assert rows[HELD]["waitlist_only"] is True
    assert rows[CONTROL]["waitlist_only"] is False


# ── The page ──────────────────────────────────────────────────────────────────

def test_the_page_data_shows_its_price(client):
    """Glen, 2026-11-10: the product page shows its price and its order button again."""
    d = client.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert not d.get("waitlist_only")
    assert d["price_cents"] == 7000 and d["price"].startswith("$70")
    assert d.get("cta_url")


def test_a_held_page_data_sells_nothing(client):
    d = client.get(f"/begin/product-page-data/{HELD}").get_json()
    assert d["waitlist_only"] is True
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d and d["autoship_eligible"] is False


# ── Autoship renewals ─────────────────────────────────────────────────────────

def _autoship_run(monkeypatch, tmp_path, capsys, slug):
    """A control autoship and one holding `slug`, both due, through the charge job."""
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
        for key, items in (("control", [{"slug": CONTROL, "qty": 1}]),
                           ("other", [{"slug": CONTROL, "qty": 1}, {"slug": slug, "qty": 1}])):
            ids[key] = subs.create(cx, email=f"{key}@x.com", stripe_customer_id="cus_t",
                                   stripe_payment_method_id="pm_t", items=items,
                                   cadence_months=1, ship_address={"state": "CA", "country": "US"},
                                   next_charge_date="2000-01-01")
        cx.commit()
    r = app.app.test_client().post("/api/cron/charge-subscriptions",
                                   headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    out = capsys.readouterr().out
    with sqlite3.connect(path) as cx:
        dates = dict(cx.execute("SELECT id, next_charge_date FROM subscriptions").fetchall())
    return r, charged, ids, out, dates


def test_the_autoship_charge_job_charges_it_and_moves_the_date(monkeypatch, tmp_path, capsys):
    """Glen, 2026-11-10: an existing autoship holding Alkalize is charged again and its
    date moves on, like the control autoship in the same run."""
    r, charged, ids, out, dates = _autoship_run(monkeypatch, tmp_path, capsys, SLUG)
    assert sorted(charged) == sorted([str(ids["control"]), str(ids["other"])]), (
        r.get_json(), out[-2500:])
    assert REFUSAL not in out and "not ready to order" not in out
    assert dates[ids["other"]] != "2000-01-01"
    assert dates[ids["control"]] != "2000-01-01"


def test_the_autoship_charge_job_refuses_a_held_product_and_keeps_the_date(
        monkeypatch, tmp_path, capsys):
    """An autoship holding a still-held product is not charged and keeps its date. The
    control autoship in the same run is charged, so the job did run."""
    r, charged, ids, out, dates = _autoship_run(monkeypatch, tmp_path, capsys, HELD)
    assert charged == [str(ids["control"])], (r.get_json(), out[-2500:])
    assert HELD_REFUSAL in out
    assert dates[ids["other"]] == "2000-01-01"
    assert dates[ids["control"]] != "2000-01-01"


# ── The waiting list (Glen approved intro and consent, 2026-10-20) ────────────
# The Alkalize wording stays pinned: its sign-ups are kept (2026-11-10). The list is
# closed to new sign-ups now Alkalize is orderable. The sign-up machinery is tested on
# Fungal Nail Repair, which is still waitlist_only.

ALK_WORDS = {
    "product": "Alkalize Bicarbonate Blend",
    "consent": ("We will email you when Alkalize Bicarbonate Blend is ready to order. "
                "Joining this list does not add you to any other mailing list."),
    "intro": ("Alkalize Bicarbonate Blend is not ready to order yet. Join the waiting list "
              "and we will email you when it is ready. No card, no charge."),
    "reserve_line": "",
    "subject": "Confirm your Alkalize Bicarbonate Blend email",
    "body_lead": ("Please confirm you'd like an email when Alkalize Bicarbonate Blend is "
                  "ready to order:"),
    "page_title": "Confirm your Alkalize Bicarbonate Blend email",
    "success": "We'll email you when Alkalize Bicarbonate Blend is ready.",
}


def test_the_list_and_its_wording_are_pinned_literally():
    from dashboard import product_waitlist as pw
    assert pw.LISTS[SLUG] == "alkalize-bicarbonate-blend-waitlist"
    assert pw.TEXTS[SLUG] == ALK_WORDS
    assert set(pw.TEXTS[SLUG]) == set(pw.TEXTS["fungal-nail-repair"])


FNR_WORDS = {
    "product": "Fungal Nail Repair",
    "consent": ("We will email you when Fungal Nail Repair is ready to order. Joining this "
                "list does not add you to any other mailing list."),
    "intro": ("Fungal Nail Repair is not ready to order yet. Join the waiting list and we "
              "will email you when it is ready. No card, no charge."),
    "reserve_line": "",
    "subject": "Confirm your Fungal Nail Repair email",
    "body_lead": "Please confirm you'd like an email when Fungal Nail Repair is ready to order:",
    "page_title": "Confirm your Fungal Nail Repair email",
    "success": "We'll email you when Fungal Nail Repair is ready.",
}


def test_the_substitute_list_wording_is_pinned_literally():
    from dashboard import product_waitlist as pw
    assert pw.TEXTS[HELD] == FNR_WORDS


@pytest.fixture()
def list_client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app._init_people_table()
    from dashboard.chat_limits import VelocityLimiter
    monkeypatch.setattr(app, "_waitlist_velocity", VelocityLimiter())
    monkeypatch.setattr(app, "_founding_enabled", lambda: False)
    sent = []
    # Every call, keyword arguments included, so a second send or a changed sender shows.
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda *a, **k: sent.append((a, k)) or {"ok": True})
    app.app.config["TESTING"] = True
    return app.app.test_client(), sent


def test_the_alkalize_page_no_longer_offers_the_list_and_sends_nothing(list_client):
    """Glen, 2026-11-10: orderable again, so its page shows no waiting-list form."""
    c, sent = list_client
    d = c.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert not d.get("waitlist")
    assert ALK_WORDS["intro"] not in json.dumps(d)
    assert sent == []


def test_an_alkalize_sign_up_is_closed_and_sends_nothing(list_client):
    """Glen, 2026-11-10: the list is closed to new sign-ups, and none is stored."""
    c, sent = list_client
    r = c.post(f"/api/waitlist/{SLUG}", json={"email": "a@x.com", "first_name": "Ann"})
    assert r.status_code == 404 and sent == []
    with sqlite3.connect(app.LOG_DB) as db:
        n = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='product_waitlist'"
                       ).fetchone()[0]
        if n:
            assert db.execute("SELECT COUNT(*) FROM product_waitlist WHERE email='a@x.com'"
                              ).fetchone()[0] == 0


def test_the_page_offers_the_list_and_sends_nothing(list_client):
    c, sent = list_client
    d = c.get(f"/begin/product-page-data/{HELD}").get_json()
    assert d["waitlist"]["slug"] == HELD
    assert d["waitlist"]["intro"] == FNR_WORDS["intro"]
    assert d["waitlist"]["consent"] == FNR_WORDS["consent"]
    assert "Scar" not in json.dumps(d["waitlist"]) and "Alkalize" not in json.dumps(d["waitlist"])
    assert sent == []




@pytest.mark.parametrize("slug", [HELD, "scar-soft-drink"])
def test_a_sign_up_sends_exactly_one_email_from_the_same_sender(list_client, slug):
    """One send, to the typed address, positional (to, subject, body) and NO keyword
    arguments: inbox.send_email's default sender, Glen's Gmail. Scar's list is the
    reference path and must send the same way."""
    from dashboard import product_waitlist as pw
    c, sent = list_client
    r = c.post(f"/api/waitlist/{slug}", json={"email": "a@x.com", "first_name": "Ann"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert len(sent) == 1, sent
    (to, subject, body), kwargs = sent[0]
    assert kwargs == {}
    assert to == "a@x.com" and subject == pw.TEXTS[slug]["subject"]
    assert body.startswith(f"Hi Ann,\n\n{pw.TEXTS[slug]['body_lead']}\n")
    if slug == HELD:
        assert subject == FNR_WORDS["subject"]
        assert "Alkalize" not in subject + body and "Scar" not in subject + body
        with sqlite3.connect(app.LOG_DB) as db:
            assert db.execute("SELECT consent_text FROM product_waitlist WHERE email='a@x.com'"
                              ).fetchone()[0] == FNR_WORDS["consent"]


def test_a_repeat_sign_up_sends_nothing_until_the_resend_window(list_client):
    from dashboard import product_waitlist as pw
    c, sent = list_client
    codes = [c.post(f"/api/waitlist/{HELD}", json={"email": "r@x.com"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 200] and len(sent) == 1
    # Past the resend window, one more, and still to the same address.
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(minutes=pw.RESEND_MINUTES + 1)).isoformat()
    with sqlite3.connect(app.LOG_DB) as db:
        db.execute("UPDATE product_waitlist SET confirm_sent_at=? WHERE email='r@x.com'", (old,))
    from dashboard.chat_limits import VelocityLimiter
    app._waitlist_velocity = VelocityLimiter()   # the per-visitor rate limit is not under test
    assert c.post(f"/api/waitlist/{HELD}", json={"email": "r@x.com"}).status_code == 200
    assert len(sent) == 2 and sent[1][0][0] == "r@x.com"


def test_a_failed_send_keeps_the_sign_up_and_sends_once(list_client, monkeypatch):
    c, sent = list_client
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise RuntimeError("gmail down")
    monkeypatch.setattr(app._inbox, "send_email", _boom)
    r = c.post(f"/api/waitlist/{HELD}", json={"email": "f@x.com"})
    assert r.status_code == 200 and len(calls) == 1
    with sqlite3.connect(app.LOG_DB) as db:
        assert db.execute("SELECT COUNT(*) FROM product_waitlist WHERE email='f@x.com'"
                          ).fetchone()[0] == 1
