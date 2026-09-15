"""Stripe cancel return for a storefront cart checkout, 2026-09-15.

api_cart_checkout marks the cart 'ordered' as soon as the Stripe session exists, so a buyer
who backed out at Stripe found an empty cart and a sign-in page. Glen: "build the cart cancel
fix". /begin/cart/cancelled?ref= gives the cart back only when nothing was paid, expires the
open Stripe session first so it cannot be paid later, cancels the unpaid order, and always
redirects to /begin/cart?checkout=cancelled.
"""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import sqlite3

import pytest

import app
import dashboard.orders as O
from dashboard import cart_store as CS
from dashboard import stripe_pay as SP

REF = "a" * 32
EMAIL = "buyer@x.com"
TARGET = "/begin/cart?checkout=cancelled"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    O.init_orders_table(cx)
    cx.close()
    return path


@pytest.fixture()
def stripe(monkeypatch):
    state = {"sessions": [{"id": "cs_1", "status": "open", "payment_status": "unpaid"}],
             "expired": [], "raise": False}

    def sessions_for_invoice(ref, **kw):
        if state["raise"]:
            raise RuntimeError("stripe down")
        return [dict(s) for s in state["sessions"]] if ref == REF else []

    def expire_session(sid):
        state["expired"].append(sid)
        return {}

    monkeypatch.setattr(SP, "sessions_for_invoice", sessions_for_invoice)
    monkeypatch.setattr(SP, "expire_session", expire_session)
    return state


def _ordered_cart(db, token="tok-1", pay_status=None):
    cx = sqlite3.connect(db)
    CS.get_or_create(cx, token, email=EMAIL)
    CS.add_item(cx, token, "brain-boost", qty=2)
    assert CS.claim_for_checkout(cx, token)
    CS.mark_ordered(cx, token, REF)
    oid = O.upsert_order(cx, source="reorder", external_ref=REF, email=EMAIL, total_cents=6997)
    if pay_status:
        cx.execute("UPDATE orders SET pay_status=? WHERE external_ref=?", (pay_status, REF))
        cx.commit()
    cx.close()
    return token


def _cart(db, token):
    cx = sqlite3.connect(db)
    row = cx.execute("SELECT status, checkout_ref FROM carts WHERE token=?", (token,)).fetchone()
    qty = cx.execute("SELECT COALESCE(SUM(qty),0) FROM cart_items WHERE token=?", (token,)).fetchone()[0]
    return row[0], row[1], qty


def _order_status(db):
    return sqlite3.connect(db).execute(
        "SELECT status FROM orders WHERE external_ref=?", (REF,)).fetchone()[0]


def _get(ref=REF):
    return app.app.test_client().get(f"/begin/cart/cancelled?ref={ref}")


def test_an_unpaid_cancel_gives_the_cart_back_and_cancels_the_order(db, stripe):
    token = _ordered_cart(db)
    r = _get()
    assert r.status_code == 302 and r.headers["Location"].endswith(TARGET)
    assert _cart(db, token) == ("open", "", 2)
    assert _order_status(db) == "cancelled"
    assert stripe["expired"] == ["cs_1"]          # the abandoned page can no longer be paid


def test_a_paid_session_changes_nothing(db, stripe):
    token = _ordered_cart(db)
    stripe["sessions"] = [{"id": "cs_1", "status": "complete", "payment_status": "paid"}]
    assert _get().status_code == 302
    assert _cart(db, token)[0] == "ordered"
    assert _order_status(db) == "new"
    assert stripe["expired"] == []


def test_a_paid_order_row_changes_nothing(db, stripe):
    token = _ordered_cart(db, pay_status="paid")
    _get()
    assert _cart(db, token)[0] == "ordered"
    assert stripe["expired"] == []


def test_a_stripe_error_changes_nothing(db, stripe):
    token = _ordered_cart(db)
    stripe["raise"] = True
    assert _get().status_code == 302
    assert _cart(db, token)[0] == "ordered" and _order_status(db) == "new"


def test_no_session_found_changes_nothing(db, stripe):
    token = _ordered_cart(db)
    stripe["sessions"] = []
    _get()
    assert _cart(db, token)[0] == "ordered" and _order_status(db) == "new"


def test_a_bad_or_unknown_ref_only_redirects(db, stripe):
    token = _ordered_cart(db)
    for ref in ("not-a-ref", "b" * 32):
        r = _get(ref)
        assert r.status_code == 302 and r.headers["Location"].endswith(TARGET)
    assert _cart(db, token)[0] == "ordered"
    assert stripe["expired"] == []


def test_a_replayed_link_is_a_no_op(db, stripe):
    token = _ordered_cart(db)
    _get()
    stripe["expired"].clear()
    _get()
    assert _cart(db, token) == ("open", "", 2)
    assert stripe["expired"] == []


def test_a_newer_open_cart_gets_the_items_folded_in(db, stripe):
    token = _ordered_cart(db)
    cx = sqlite3.connect(db)
    CS.get_or_create(cx, "tok-2", email=EMAIL)
    CS.add_item(cx, "tok-2", "wholomega", qty=1)
    cx.close()
    _get()
    assert _cart(db, token)[0] == "merged"
    status, _, qty = _cart(db, "tok-2")
    assert status == "open" and qty == 3


def test_reopen_needs_the_matching_checkout_ref(db):
    token = _ordered_cart(db)
    cx = sqlite3.connect(db)
    assert CS.reopen_cancelled_checkout(cx, token, EMAIL, "c" * 32) == "none"
    assert CS.reopen_cancelled_checkout(cx, token, EMAIL, REF) == "reopened"


def test_a_wrong_ref_never_folds_items_into_a_newer_cart(db):
    """The fold path has no SQL guard of its own, so the ref check must stop it."""
    token = _ordered_cart(db)
    cx = sqlite3.connect(db)
    CS.get_or_create(cx, "tok-2", email=EMAIL)
    CS.add_item(cx, "tok-2", "wholomega", qty=1)
    assert CS.reopen_cancelled_checkout(cx, token, EMAIL, "c" * 32) == "none"
    assert _cart(db, token)[0] == "ordered"
    assert _cart(db, "tok-2")[2] == 1


def test_cancel_unpaid_checkout_never_touches_a_paid_order(db):
    _ordered_cart(db, pay_status="paid")
    cx = sqlite3.connect(db)
    oid = cx.execute("SELECT id FROM orders WHERE external_ref=?", (REF,)).fetchone()[0]
    assert O.cancel_unpaid_checkout(cx, oid) == 0
    assert _order_status(db) == "new"


def test_the_cart_checkout_asks_for_the_cart_cancel_return(db, monkeypatch):
    seen = {}

    def fake(email, cart, **kw):
        seen.update(kw)
        return {"out": {"invoice_id": REF, "total": 69.97}, "stripe_url": "https://stripe.test/s"}

    monkeypatch.setattr(app, "_checkout_cart", fake)
    monkeypatch.setattr(app, "is_member", lambda sid, email: True)
    monkeypatch.setattr(app, "_cart_email", lambda: EMAIL)
    monkeypatch.setattr(app, "_get_product", lambda slug: {"slug": slug, "name": "B", "price_cents": 6997})
    c = app.app.test_client()
    c.post("/api/cart/add", json={"slug": "brain-boost"})
    c.post("/api/cart/checkout", json={"address": {"name": "A", "street": "1 Main", "city": "Hilo",
                                                   "state": "HI", "zip": "96720", "country": "US"}})
    assert seen.get("cancel_to_cart") is True


def test_checkout_cart_puts_the_ref_in_the_cancel_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    monkeypatch.setattr(app, "_resolve_checkout_coupon_pct", lambda code, email: (0, None))
    monkeypatch.setattr(app, "_is_paid_member", lambda email: False)
    monkeypatch.setattr(app, "_price_cart", lambda cart, **kw: {
        "qbo_lines": [{"x": 1}], "discount_cents": 0, "points_redeemed_cents": 0, "shipping_cents": 0,
        "items_rec": [], "priced": {"subtotal_cents": 6997, "total_cents": 6997, "get_cents": 0}})
    monkeypatch.setattr(app, "_plan_ship_credit", lambda email, cents: 0)
    monkeypatch.setattr(app, "_stripe_checkout_url_for_reorder",
                        lambda out, email: captured.update(out) or "https://stripe.test/s")
    monkeypatch.setattr(app, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(app, "_record_referral_if_any", lambda *a, **k: None)
    monkeypatch.setattr(app._bos_orders, "set_order_qbo_lines", lambda *a, **k: None)
    app._checkout_cart(EMAIL, [{"slug": "brain-boost", "qty": 1}], ship={}, cancel_to_cart=True)
    assert captured["cancel_url"].endswith(f"/begin/cart/cancelled?ref={captured['invoice_id']}")
    captured.clear()
    app._checkout_cart(EMAIL, [{"slug": "brain-boost", "qty": 1}], ship={})
    assert "cancel_url" not in captured             # reorder and other callers keep /reorder
