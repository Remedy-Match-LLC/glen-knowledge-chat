"""Alkalize Bicarbonate Blend cannot be bought anywhere (2026-10-20).

Glen, 2026-10-20: Alkalize is out of stock and being reformulated. "You can keep the link.
Take back orders, or a waiting list." It carries `waitlist_only`, the same flag as Scar
Soft Drink and Fungal Nail Repair, so the one predicate (practitioner_portal.is_orderable)
refuses it at every cart, checkout, invoice and autoship path. The slug and page stay.

Each test reads the REAL record from data/products.json, so removing the flag fails here.
"""
import json
import os
import sqlite3

import pytest

import app
from dashboard import cart_store as CS
from dashboard import client_invoice_lines as cil
from dashboard import practitioner_portal as pp
from dashboard import wholesale_checkout as wc

SLUG = "alkalize-bicarbonate-blend"
CONTROL = "magnesium-syntropy"
REFUSAL = ("Alkalize Bicarbonate Blend is not ready to order yet. "
           "Join the waiting list on its page.")
URL = f"/begin/product/{SLUG}"


def _real():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    """The real records, copied into app's catalog and the pricing catalog, so a test
    that replaced app's products earlier in the run cannot change the answer."""
    products = app._PRODUCTS.setdefault("products", {})
    real = _real()
    for s in (SLUG, CONTROL):
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


def _refused(r, code=400):
    assert r.status_code == code, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert REFUSAL in (body.get("error") or ""), body
    assert body.get("url") == URL, body


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

def test_the_record_is_waitlist_only_and_keeps_its_page():
    p = _real()[SLUG]
    assert p["waitlist_only"] is True
    # Not `out_of_stock` as well: with waitlist_only set it changes nothing, and the
    # out-of-stock test pins Vitamin D Syntropy as the only product carrying it.
    assert not p.get("out_of_stock") and not p.get("inactive")
    assert p["url"] == "https://myhealingoasis.com/begin/product/alkalize-bicarbonate-blend"


def test_the_predicate_refuses_it_and_not_the_control(catalog):
    assert pp.is_orderable(SLUG, catalog) is False
    assert pp.is_orderable(CONTROL, catalog) is True
    msg = pp.waitlist_refusal(pp.waitlist_block(SLUG, catalog))
    assert msg == REFUSAL and msg.url == URL


# ── Cart and checkout ─────────────────────────────────────────────────────────

def test_price_cart_refuses_before_pricing():
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": CONTROL, "qty": 1}, {"slug": SLUG, "qty": 1}],
                        ship={"country": "US"})
    assert REFUSAL in str(e.value)


def test_buy_page_sends_the_buyer_to_the_product_page(client):
    r = client.get(f"/begin/buy/{SLUG}")
    assert r.status_code == 302 and r.headers["Location"].endswith(URL)


def test_begin_checkout_is_refused_before_any_payment(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    _refused(client.post(f"/begin/checkout/{SLUG}", json={"email": "a@x.com", "method": "card"}))
    assert calls == []


def test_cart_add_is_refused(client, logdb):
    _refused(client.post("/api/cart/add", json={"slug": SLUG}))


def test_reorder_subscribe_is_refused(client, monkeypatch):
    calls = _no_payment(monkeypatch)
    monkeypatch.setattr(app, "_reorder_email_from_cookie", lambda: "a@x.com")
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    monkeypatch.setattr(app, "_subscriptions_enabled", lambda: True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    monkeypatch.setenv("PRICING_ENGINE_CHECKOUT", "1")
    _refused(client.post("/reorder/subscribe", json={
        "cadence_months": 1, "items": [{"slug": SLUG, "qty": 1}],
        "address": {"street": "1 A St", "country": "US"}}))
    assert calls == []


# ── Invoices ──────────────────────────────────────────────────────────────────

def test_invoice_rebuild_refuses_a_posted_line_whole():
    with pytest.raises(cil.NotOrderable):
        cil.rebuild([{"slug": CONTROL, "qty": 1}, {"slug": SLUG, "qty": 2}], {},
                    known=lambda s: True, refusal=app._waitlist_refusal)


def test_wholesale_order_is_refused_before_credit_is_redeemed(monkeypatch, catalog):
    redeemed = []
    monkeypatch.setattr(wc.wallet, "redeem_for_order", lambda *a, **k: redeemed.append(a) or 0)
    out = wc.build_order([{"slug": SLUG, "qty": 1}], {"id": "p1", "modules_completed": 1},
                         catalog=catalog)
    assert out["ok"] is False and out["error"] == REFUSAL and out["url"] == URL
    assert redeemed == []


def test_console_biofield_catalog_marks_it_not_ready(monkeypatch, catalog):
    monkeypatch.setattr(app._bos_products, "catalog", lambda **kw: [
        dict(catalog[s], slug=s) for s in (SLUG, CONTROL)])
    monkeypatch.setattr(app, "_portal_console_ok", lambda: True)
    rows = {p["slug"]: p for p in
            app.app.test_client().get("/api/console/biofield-portal/catalog").get_json()["products"]}
    assert rows[SLUG]["waitlist_only"] is True
    assert rows[CONTROL]["waitlist_only"] is False


# ── The page ──────────────────────────────────────────────────────────────────

def test_the_page_data_sells_nothing(client):
    d = client.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert d["waitlist_only"] is True
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d and d["autoship_eligible"] is False


# ── Autoship renewals ─────────────────────────────────────────────────────────

def test_the_autoship_charge_job_refuses_it_and_keeps_the_date(monkeypatch, tmp_path, capsys):
    """An existing autoship holding Alkalize is not charged and keeps its date. A control
    autoship in the same run is charged, so the job did run."""
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
                           ("alk", [{"slug": CONTROL, "qty": 1}, {"slug": SLUG, "qty": 1}])):
            ids[key] = subs.create(cx, email=f"{key}@x.com", stripe_customer_id="cus_t",
                                   stripe_payment_method_id="pm_t", items=items,
                                   cadence_months=1, ship_address={"state": "CA", "country": "US"},
                                   next_charge_date="2000-01-01")
        cx.commit()
    r = app.app.test_client().post("/api/cron/charge-subscriptions",
                                   headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    out = capsys.readouterr().out
    assert charged == [str(ids["control"])], (r.get_json(), out[-2500:])
    assert REFUSAL in out
    with sqlite3.connect(path) as cx:
        dates = dict(cx.execute("SELECT id, next_charge_date FROM subscriptions").fetchall())
    assert dates[ids["alk"]] == "2000-01-01"
    assert dates[ids["control"]] != "2000-01-01"


# ── The waiting list (Glen approved intro and consent, 2026-10-20) ────────────

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


def test_the_page_offers_the_list_and_sends_nothing(list_client):
    c, sent = list_client
    d = c.get(f"/begin/product-page-data/{SLUG}").get_json()
    assert d["waitlist"]["slug"] == SLUG
    assert d["waitlist"]["intro"] == ALK_WORDS["intro"]
    assert d["waitlist"]["consent"] == ALK_WORDS["consent"]
    assert "Scar" not in json.dumps(d["waitlist"]) and "Fungal" not in json.dumps(d["waitlist"])
    assert sent == []




@pytest.mark.parametrize("slug", [SLUG, "scar-soft-drink"])
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
    if slug == SLUG:
        assert subject == ALK_WORDS["subject"]
        assert "Fungal" not in subject + body and "Scar" not in subject + body
        with sqlite3.connect(app.LOG_DB) as db:
            assert db.execute("SELECT consent_text FROM product_waitlist WHERE email='a@x.com'"
                              ).fetchone()[0] == ALK_WORDS["consent"]


def test_a_repeat_sign_up_sends_nothing_until_the_resend_window(list_client):
    from dashboard import product_waitlist as pw
    c, sent = list_client
    codes = [c.post(f"/api/waitlist/{SLUG}", json={"email": "r@x.com"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 200] and len(sent) == 1
    # Past the resend window, one more, and still to the same address.
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(minutes=pw.RESEND_MINUTES + 1)).isoformat()
    with sqlite3.connect(app.LOG_DB) as db:
        db.execute("UPDATE product_waitlist SET confirm_sent_at=? WHERE email='r@x.com'", (old,))
    from dashboard.chat_limits import VelocityLimiter
    app._waitlist_velocity = VelocityLimiter()   # the per-visitor rate limit is not under test
    assert c.post(f"/api/waitlist/{SLUG}", json={"email": "r@x.com"}).status_code == 200
    assert len(sent) == 2 and sent[1][0][0] == "r@x.com"


def test_a_failed_send_keeps_the_sign_up_and_sends_once(list_client, monkeypatch):
    c, sent = list_client
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise RuntimeError("gmail down")
    monkeypatch.setattr(app._inbox, "send_email", _boom)
    r = c.post(f"/api/waitlist/{SLUG}", json={"email": "f@x.com"})
    assert r.status_code == 200 and len(calls) == 1
    with sqlite3.connect(app.LOG_DB) as db:
        assert db.execute("SELECT COUNT(*) FROM product_waitlist WHERE email='f@x.com'"
                          ).fetchone()[0] == 1
