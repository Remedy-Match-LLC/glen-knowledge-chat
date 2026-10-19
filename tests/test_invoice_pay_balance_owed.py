"""An invoice with a balance owed shows the pay button and asks for the balance only.

Glen, 2026-10-19: "fix the button". Ashley King's order 230 was paid $433 by card,
then Rae added a $40 bottle. The order stayed pay_status "paid", so the invoice hid
the pay button though $40 was owed. Its $433 sat on the order row with no ledger
row, so a $40 ledger row alone would have shown $433 owed: the tests below pin that.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import invoice_payability as ip

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))


# ── the rule ────────────────────────────────────────────────────────────────

def test_a_paid_order_with_a_balance_is_payable():
    assert ip.is_payable("new", "paid", 4000) is True
    assert ip.is_payable("shipped", "paid", 1) is True


def test_a_paid_order_with_no_balance_is_not():
    assert ip.is_payable("new", "paid", 0) is False
    assert ip.is_payable("done", "paid", -500) is False


def test_cancelled_refunded_and_junk_never_open_the_button():
    assert ip.is_payable("cancelled", "paid", 4000) is False
    assert ip.is_payable("done", "refunded", 4000) is False
    assert ip.is_payable("new", "paid", "lots") is False
    assert ip.is_payable("", "paid", 4000) is False


def test_without_a_balance_the_old_rule_holds():
    assert ip.is_payable("new", "paid") is False
    assert ip.is_payable("new", "unpaid") is True


# ── the routes ──────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    app_module = importlib.import_module("app")
    db = str(tmp_path / "pay.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    from dashboard import practitioner_portal as PP
    monkeypatch.setattr(PP, "_LOG_DB", Path(db))
    monkeypatch.setattr(app_module, "INVOICE_PAYLINK_ENABLED", True)
    monkeypatch.setattr(app_module, "_STRIPE_ACTIVE", True)
    sent = []
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: sent.append(a))
    from dashboard import stripe_pay
    made = []

    def _session(amount, **k):
        made.append({"amount": amount, **k})
        return {"url": "https://stripe.test/s"}
    monkeypatch.setattr(stripe_pay, "create_checkout_session", _session)
    from dashboard import orders as O, order_payments as OP
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    OP.ensure_table(cx)

    def order(**kw):
        oid = O.upsert_order(cx, source=kw.pop("source", "dropship"),
                             external_ref=kw.pop("ref"), email="t@example.com",
                             total_cents=kw.pop("total"), status=kw.pop("status", "new"))
        sets = ", ".join(f"{k}=?" for k in kw)
        if sets:
            cx.execute(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))
        cx.commit()
        return oid, PP.create_order_invoice_token(oid, db_path=db)

    return dict(app=app_module, c=app_module.app.test_client(), cx=cx, O=O, OP=OP,
                order=order, made=made, sent=sent, stripe=stripe_pay, mp=monkeypatch)


def _like_230(env):
    """Paid $433 on the order row, no ledger row, total raised to $473."""
    return env["order"](ref="DS-230", total=47300, pay_status="paid", pay_method="card",
                        paid_cents=43300, paid_at="2026-10-16T21:15:13Z",
                        stripe_payment_intent="pi_original")


def test_order_230_shows_the_button_for_40_dollars(env):
    _oid, tok = _like_230(env)
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["payable"] is True
    assert o["balance_due_cents"] == 4000


def test_card_charges_the_balance_on_its_own_path(env):
    _oid, tok = _like_230(env)
    r = env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 200, r.get_json()
    s = env["made"][-1]
    assert s["amount"] == 4000
    md = s["metadata"]
    assert md["kind"] == "invoice-balance" and "invoice_id" not in md


def test_zelle_asks_for_the_balance(env):
    _oid, tok = _like_230(env)
    j = env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "zelle"}).get_json()
    assert "$40.00" in j["instructions"] and "$473" not in j["instructions"]


def _return(env, oid, pi="pi_balance", amount=4000):
    env["mp"].setattr(env["stripe"], "get_session", lambda sid: {
        "id": sid, "payment_status": "paid", "amount_total": amount,
        "payment_intent": pi,
        "metadata": {"kind": "invoice-balance", "order_id": str(oid),
                     "balance_ref": "DS-230", "customer_id": ""}})
    return env["c"].get("/begin/checkout-return?session_id=cs_test_1")


def test_a_paid_balance_settles_the_order_and_changes_nothing_else(env):
    oid, tok = _like_230(env)
    _return(env, oid)
    _return(env, oid)     # a reload of the return page records nothing twice
    rows = env["OP"].list_payments(env["cx"], oid)
    assert sorted((r["source"], r["amount_cents"]) for r in rows) == [
        ("legacy", 43300), ("stripe", 4000)]
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0
    o = env["O"].get_order(env["cx"], oid)
    assert (o["status"], o["pay_status"], o["paid_cents"], o["stripe_payment_intent"]) == (
        "new", "paid", 43300, "pi_original")
    after = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert after["payable"] is False


def test_a_balance_payment_on_an_order_with_a_ledger_adds_no_legacy_row(env):
    oid, _tok = _like_230(env)
    env["OP"].add_payment(env["cx"], oid, 43300, "card", source="stripe",
                          external_ref="pi_original")
    _return(env, oid)
    assert sorted(r["source"] for r in env["OP"].list_payments(env["cx"], oid)) == [
        "stripe", "stripe"]


def test_a_fully_paid_order_still_has_no_button(env):
    _oid, tok = env["order"](ref="DS-1", total=43300, pay_status="paid",
                             paid_cents=43300, pay_method="card")
    assert env["c"].get(f"/api/invoice/{tok}").get_json()["order"]["payable"] is False
    assert env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"}).status_code == 409


def test_a_first_payment_on_an_open_invoice_keeps_its_path(env):
    _oid, tok = env["order"](ref="INH-OPEN", total=14314, status="proposed",
                             source="in-house")
    env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    s = env["made"][-1]
    assert s["amount"] == 14314
    assert s["metadata"]["kind"] == "in-house" and s["metadata"]["invoice_id"] == "INH-OPEN"


def test_a_part_paid_open_invoice_asks_for_the_rest(env):
    oid, tok = env["order"](ref="INH-PART", total=10000, status="proposed",
                            source="in-house")
    env["OP"].add_payment(env["cx"], oid, 2500, "zelle", source="manual")
    env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert env["made"][-1]["amount"] == 7500
    assert env["made"][-1]["metadata"]["kind"] == "invoice-balance"


def test_claiming_a_balance_keeps_the_order_paid(env):
    oid, tok = _like_230(env)
    r = env["c"].post(f"/api/invoice/{tok}/claim-paid", json={"method": "zelle"})
    assert r.get_json()["ok"] is True
    assert env["O"].get_order(env["cx"], oid)["pay_status"] == "paid"
    assert "$40.00" in env["sent"][-1][2]


def test_the_page_asks_for_the_balance():
    html = (repo / "static" / "invoice.html").read_text()
    body = html[html.index("function setupPay"):html.index("async function pay(")]
    assert "money(due)" in body and "money(ORDER.total_cents)" not in body
