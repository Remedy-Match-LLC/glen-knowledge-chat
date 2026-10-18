"""The single-product buy page (POST /begin/checkout/<slug>) and its Zelle / Wise orders.

Order 217 (2026-10-09, Rae: "paid by zelle but not through the system because it
wasn't working properly") came through this page. Three defects:
- the order was saved with no pay_method, so the 24-hour abandoned-checkout expiry
  cancelled an unpaid Zelle order, and the Zelle import then could not match it;
- the page asked for the invoice number as the memo and showed none;
- the page promised an email that nothing sends.
"""
import sqlite3

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app
from dashboard import order_payments
from dashboard import orders as O
from dashboard import qbo_billing

PRODUCT_SLUG = "brain-boost"
ROOT = Path(__file__).resolve().parent.parent


def _prep(monkeypatch, tmp_path):
    db = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    cx = sqlite3.connect(db)
    try:
        O.init_orders_table(cx)
        order_payments.ensure_table(cx)
        cx.commit()
    finally:
        cx.close()
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)

    def boom(*a, **k):
        raise AssertionError("begin_checkout must not call create_invoice")
    monkeypatch.setattr(qbo_billing, "create_invoice", boom)
    monkeypatch.setattr(app, "is_member", lambda sid, email: True)
    monkeypatch.setattr(
        app, "_get_product",
        lambda s: {"slug": s, "name": "Brain Boost", "price_cents": 7000,
                   "qty_pricing": True, "qbo_item_id": "27"} if s == PRODUCT_SLUG else None)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 2295})
    import dashboard.stripe_pay as sp
    monkeypatch.setattr(sp, "create_checkout_session", lambda *a, **k: {"url": "https://s.test"})
    return db


def _buy(method):
    return app.app.test_client().post(
        f"/begin/checkout/{PRODUCT_SLUG}",
        json={"email": "c@b.com", "name": "C Buyer", "qty": 1, "method": method,
              "address": {"state": "CA", "country": "US", "name": "C Buyer"}})


def _rows(db):
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in cx.execute(
            "SELECT id, source, pay_method, status FROM orders").fetchall()]
    finally:
        cx.close()


def test_zelle_order_records_its_method_and_shows_the_order_number(monkeypatch, tmp_path):
    db = _prep(monkeypatch, tmp_path)
    r = _buy("zelle")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["source"] == "funnel"
    assert rows[0]["pay_method"] == "zelle"
    assert body["order_id"] == rows[0]["id"]
    assert body["pay_instructions"]["memo"] == f"Order {rows[0]['id']}"
    assert body["pay_instructions"]["to"] == app._ALT_PAY["zelle"]["to"]


def test_wise_order_records_its_method(monkeypatch, tmp_path):
    db = _prep(monkeypatch, tmp_path)
    r = _buy("wise")
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = _rows(db)
    assert rows[0]["pay_method"] == "wise"
    assert r.get_json()["pay_instructions"]["memo"] == f"Order {rows[0]['id']}"


def test_the_shared_instructions_are_not_mutated(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    assert _buy("zelle").status_code == 200
    assert "memo" not in app._ALT_PAY["zelle"]


def test_card_order_keeps_no_pay_method(monkeypatch, tmp_path):
    db = _prep(monkeypatch, tmp_path)
    r = _buy("card")
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = _rows(db)
    assert rows and rows[0]["pay_method"] is None
    assert "order_id" not in r.get_json()


def _watch_effects(monkeypatch):
    """Record the referral, coupon and journey writes that name the checkout."""
    calls = []
    monkeypatch.setattr(app, "_best_active_self_coupon_code", lambda e, s: "SELF10")
    monkeypatch.setattr(app, "_resolve_self_coupon_pct",
                        lambda code, slug: (10, {"code": code}) if code else (0, None))
    monkeypatch.setattr(app, "_record_referral_if_any",
                        lambda ctx, email, ref: calls.append("referral"))
    from dashboard import coupons
    monkeypatch.setattr(coupons, "mark_redeemed",
                        lambda cx, code, *, order_ref: calls.append("coupon"))
    monkeypatch.setattr(app.begin_funnel, "record_unlock",
                        lambda cx, **k: calls.append("unlock"))
    return calls


@pytest.mark.parametrize("method", ["zelle", "wise"])
def test_unsaved_alt_pay_order_gets_no_instructions_and_spends_nothing(monkeypatch, tmp_path, method):
    _prep(monkeypatch, tmp_path)
    calls = _watch_effects(monkeypatch)
    monkeypatch.setattr(app, "_ingest_order", lambda **k: None)
    r = _buy(method)
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False
    assert "pay_instructions" not in body
    # The coupon must survive for the retry, and no referral names a missing order.
    assert calls == []


def test_saved_zelle_order_records_its_coupon_and_referral(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    calls = _watch_effects(monkeypatch)
    assert _buy("zelle").status_code == 200
    assert sorted(calls) == ["coupon", "referral", "unlock"]


def test_card_order_still_records_its_coupon_and_referral(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    calls = _watch_effects(monkeypatch)
    assert _buy("card").status_code == 200
    assert sorted(calls) == ["coupon", "referral", "unlock"]


@pytest.mark.parametrize("method", ["zelle", "wise"])
def test_zero_total_alt_pay_order_is_settled_with_no_instructions(monkeypatch, tmp_path, method):
    db = _prep(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_charge_cents", lambda pc: 0)
    r = _buy(method)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["no_payment_required"] is True
    assert "pay_instructions" not in body
    cx = sqlite3.connect(db)
    try:
        row = cx.execute("SELECT pay_status, total_cents, pay_method FROM orders").fetchone()
        paid = cx.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM order_payments "
                          "WHERE kind='payment'").fetchone()[0]
    finally:
        cx.close()
    assert row[:2] == ("paid", 0)
    assert row[2] != method          # not an open Zelle/Wise intent
    assert paid == 0


def test_buy_page_note_points_at_the_memo_not_an_invoice(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    for m in ("zelle", "wise"):
        note = _buy(m).get_json()["pay_instructions"]["note"]
        assert note == app._BUY_PAGE_ALT_PAY_NOTE[m]
        assert "memo above" in note and "invoice" not in note


def test_unpaid_buy_page_zelle_and_wise_orders_are_not_expired(monkeypatch, tmp_path):
    db = _prep(monkeypatch, tmp_path)
    for m in ("zelle", "wise", "card"):
        assert _buy(m).status_code == 200
    cx = sqlite3.connect(db)
    try:
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        cx.execute("UPDATE orders SET created_at=?", (old,))
        cx.commit()
        assert O.expire_abandoned_checkouts(cx) == 1
        states = {r[0]: r[1] for r in cx.execute(
            "SELECT COALESCE(pay_method, 'none'), status FROM orders").fetchall()}
    finally:
        cx.close()
    assert states == {"zelle": "new", "wise": "new", "none": "cancelled"}


def test_buy_page_shows_the_memo_and_promises_no_email():
    html = (ROOT / "static" / "begin-buy.html").read_text()
    assert 'id="pd-memo"' in html and 'id="pd-memo-value"' in html
    assert "pi.memo" in html and "cg.payInstr.memo" in html
    # Nothing on this page emails an invoice, so nothing may promise one.
    low = html.lower()
    for phrase in ("by email too", "in your email", "emailed to you", "send your invoice",
                   "invoice goes here", "invoice number"):
        assert phrase not in low, phrase
    assert "Please save these payment details. They are not sent by email." in html
    assert "if (data.no_payment_required) return;" in html


@pytest.mark.parametrize("method", ["zelle", "wise"])
def test_zero_total_order_covered_by_ship_credit_spends_the_credit(monkeypatch, tmp_path, method):
    """A $0 order is marked paid, which releases the hold on the credit covering it, so
    the credit must be spent then, or it could pay for a second order (review round 3)."""
    db = _prep(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    from dashboard import ship_credit
    cx = sqlite3.connect(db)
    try:
        app._points_init_ship(cx)
        ship_credit.grant(cx, "c@b.com", 20000, source_ref="seed")
        cx.commit()
    finally:
        cx.close()
    r = _buy(method)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["no_payment_required"] is True
    cx = sqlite3.connect(db)
    try:
        applied, pay_status = cx.execute(
            "SELECT ship_credit_applied_cents, pay_status FROM orders").fetchone()
        left = ship_credit.balance(cx, "c@b.com")
    finally:
        cx.close()
    assert pay_status == "paid"
    assert applied > 0
    assert left == 20000 - applied
