"""Glen, 2026-10-10: "fix the /begin store cart to accept Zelle."

Choosing Zelle at /api/cart/checkout mints no Stripe session. It records the order unpaid
with pay_method='zelle' (so the 24-hour abandoned-checkout expiry leaves it alone), closes
the cart, and returns the Zelle instructions with the order number as the memo. The
portal's Zelle branch (api_client_portal_checkout) is the model.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app
import dashboard.orders as O
from dashboard import cart_store as CS
from dashboard import qbo_billing

EMAIL = "zelle-buyer@x.com"
SLUG = "brain-boost"
ADDRESS = {"name": "A B", "street": "1 Main", "city": "Hilo",
           "state": "HI", "zip": "96720", "country": "US"}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    O.init_orders_table(cx)
    cx.commit(); cx.close()
    monkeypatch.setattr(app, "is_member", lambda sid, email: True)
    monkeypatch.setattr(app, "_cart_email", lambda: EMAIL)
    monkeypatch.setattr(
        app, "_get_product",
        lambda s: {"slug": s, "name": "Brain Boost", "price_cents": 7000,
                   "qty_pricing": True, "qbo_item_id": "27"} if s == SLUG else None)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 1265})
    monkeypatch.setattr(qbo_billing, "create_invoice",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no QBO invoice")))
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    stripe_calls = []
    import dashboard.stripe_pay as sp
    monkeypatch.setattr(sp, "create_checkout_session",
                        lambda *a, **k: stripe_calls.append(1) or {"url": "https://s.test"})
    c = app.app.test_client()
    c.post("/api/cart/add", json={"slug": SLUG})
    return {"db": path, "client": c, "stripe_calls": stripe_calls}


REF = "a" * 32


def _zelle(ref=REF):
    return {"address": ADDRESS, "method": "zelle", "checkout_request_id": ref}


def _orders(db):
    cx = sqlite3.connect(db); cx.row_factory = sqlite3.Row
    rows = [dict(r) for r in cx.execute("SELECT * FROM orders").fetchall()]
    cx.close()
    return rows


def _cart_status(db):
    cx = sqlite3.connect(db)
    rows = [r[0] for r in cx.execute("SELECT status FROM carts WHERE email=?", (EMAIL,))]
    cx.close()
    return rows


def test_zelle_places_an_unpaid_order_and_returns_the_instructions(env):
    r = env["client"].post("/api/cart/checkout", json=_zelle())
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["ok"] is True and d["method"] == "zelle"
    assert "stripe_url" not in d
    assert env["stripe_calls"] == []
    rows = _orders(env["db"])
    assert len(rows) == 1
    o = rows[0]
    assert o["id"] == d["order_id"]
    assert o["pay_method"] == "zelle"
    assert o["pay_status"] != "paid"
    assert o["source"] == "reorder"
    assert d["total_cents"] == o["total_cents"] == 7000 + 1265
    assert d["shipping_cents"] == 1265
    pi = d["pay_instructions"]
    assert pi["to"] == app._ALT_PAY["zelle"]["to"]
    assert pi["memo"] == f"Order {o['id']}"
    assert d["order_ref"] == o["external_ref"] == REF


def test_the_cart_is_closed_after_a_zelle_order(env):
    env["client"].post("/api/cart/checkout", json=_zelle())
    assert "open" not in _cart_status(env["db"])
    assert "checking_out" not in _cart_status(env["db"])


def test_a_day_old_zelle_order_is_not_expired(env):
    env["client"].post("/api/cart/checkout", json=_zelle())
    cx = sqlite3.connect(env["db"]); cx.row_factory = sqlite3.Row
    later = datetime.now(timezone.utc) + timedelta(hours=72)
    O.expire_abandoned_checkouts(cx, now=later)
    assert cx.execute("SELECT status FROM orders").fetchone()[0] == "new"
    cx.close()


def test_card_is_still_the_default(env):
    r = env["client"].post("/api/cart/checkout", json={"address": ADDRESS})
    d = r.get_json()
    assert r.status_code == 200 and d["stripe_url"] == "https://s.test"
    assert env["stripe_calls"] == [1]
    assert _orders(env["db"])[0]["pay_method"] in (None, "")


def test_an_unknown_method_is_refused_before_anything_happens(env):
    r = env["client"].post("/api/cart/checkout", json={"address": ADDRESS, "method": "paypal"})
    assert r.status_code == 400
    assert _orders(env["db"]) == []
    assert "open" in _cart_status(env["db"])


def test_a_zelle_order_that_did_not_save_releases_the_cart(env, monkeypatch):
    side = []
    monkeypatch.setattr(app, "_ingest_order", lambda **k: None)
    monkeypatch.setattr(app, "_record_referral_if_any", lambda *a, **k: side.append("referral"))
    monkeypatch.setattr(O, "set_order_qbo_lines", lambda *a, **k: side.append("qbo"))
    r = env["client"].post("/api/cart/checkout", json=_zelle())
    assert r.status_code == 503
    assert r.get_json()["ok"] is False
    assert "pay_instructions" not in r.get_json()
    assert "open" in _cart_status(env["db"])
    assert side == []


def test_a_cart_that_owes_nothing_takes_the_card_path(monkeypatch):
    """No Zelle instructions for a $0 charge: the order is complete already."""
    src = Path(app.__file__).read_text()
    body = src[src.index("def _checkout_cart("):]
    body = body[:body.index("\n@app.route")]
    assert 'zelle = (method == "zelle" and not no_payment_required)' in body


def test_the_cart_page_offers_zelle_and_shows_the_instructions():
    html = (Path(app.__file__).parent / "static" / "begin-cart.html").read_text()
    assert 'name="payMethod" value="card" checked' in html
    assert 'name="payMethod" value="zelle"' in html
    assert "var body = {method: payMethod()};" in html
    assert "body.checkout_request_id = zelleRef;" in html
    # The key is saved BEFORE the request goes out, so a lost reply can be looked up.
    assert html.index("setZelleRefs([zelleRef]") < html.index('post("/api/cart/checkout"')
    assert 'post("/api/cart/zelle-orders"' in html
    assert 'id="zelle-done"' in html
    assert html.index('id="zelle-done"') > html.index('id="cart-items"')
    # Only keys are stored; instructions come from the server, never stored HTML.
    assert "localStorage.setItem(ZELLE_KEY, JSON.stringify(list.slice(0, 5)))" in html
    assert "html:" not in html


def test_the_cart_uses_the_portals_zelle_wording():
    root = Path(app.__file__).parent / "static"
    cart = (root / "begin-cart.html").read_text()
    portal = (root / "client-portal.html").read_text()
    for line in ("Your Zelle payment instructions", "Memo / order reference",
                 "enter the recipient, exact amount, and memo above",
                 "Open Zelle payment page", "Find or open your bank for Zelle"):
        assert line in cart and line in portal, line


def test_zelle_needs_the_pages_request_id(env):
    for bad in (None, "", "short", "g" * 32, "A" * 31):
        body = {"address": ADDRESS, "method": "zelle"}
        if bad is not None:
            body["checkout_request_id"] = bad
        r = env["client"].post("/api/cart/checkout", json=body)
        assert r.status_code == 400, bad
    assert _orders(env["db"]) == []


def test_a_reused_request_id_is_refused(env):
    env["client"].post("/api/cart/checkout", json=_zelle())
    env["client"].post("/api/cart/add", json={"slug": SLUG})
    r = env["client"].post("/api/cart/checkout", json=_zelle())
    assert r.status_code == 409
    assert len(_orders(env["db"])) == 1


def _lookup(env, refs):
    return env["client"].post("/api/cart/zelle-orders", json={"refs": refs}).get_json()["orders"]


def test_the_page_can_look_up_its_unpaid_zelle_order(env):
    d = env["client"].post("/api/cart/checkout", json=_zelle()).get_json()
    got = _lookup(env, [REF, "b" * 32, "not-a-ref"])
    assert len(got) == 1
    assert got[0]["order_id"] == d["order_id"]
    assert got[0]["pay_instructions"] == d["pay_instructions"]
    assert got[0]["total_cents"] == d["total_cents"]


def test_a_paid_or_cancelled_order_drops_out(env):
    env["client"].post("/api/cart/checkout", json=_zelle())
    cx = sqlite3.connect(env["db"])
    cx.execute("UPDATE orders SET pay_status='paid'"); cx.commit()
    assert _lookup(env, [REF]) == []
    cx.execute("UPDATE orders SET pay_status='unpaid', status='cancelled'"); cx.commit()
    cx.close()
    assert _lookup(env, [REF]) == []


def test_another_shoppers_key_shows_nothing(env, monkeypatch):
    env["client"].post("/api/cart/checkout", json=_zelle())
    monkeypatch.setattr(app, "_cart_email", lambda: "someone-else@x.com")
    assert _lookup(env, [REF]) == []
    monkeypatch.setattr(app, "_cart_email", lambda: "")
    assert _lookup(env, [REF]) == []


def test_a_card_order_is_never_returned_as_zelle(env):
    env["client"].post("/api/cart/checkout", json={"address": ADDRESS})
    ref = _orders(env["db"])[0]["external_ref"]
    assert _lookup(env, [ref]) == []
