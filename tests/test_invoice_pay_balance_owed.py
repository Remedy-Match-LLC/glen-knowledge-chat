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
    assert o["pay_amount_cents"] == 4000 and o["balance_due_cents"] == 4000


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
    """setupPay run in node against a stub page: the button names the pay amount."""
    import json, re, shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = (repo / "static" / "invoice.html").read_text()
    fn = html[html.index("function setupPay"):html.index("async function pay(")]
    money = re.search(r"function money\(.*?\n}", html, re.S) or re.search(
        r"(const|let|var) money\s*=.*?;\n", html, re.S)
    assert money, "money() not found"
    js = ("const els={};function $(id){return els[id]||(els[id]={style:{},textContent:'',"
          "innerHTML:'',disabled:false});}\n"
          "const document={querySelectorAll:()=>[]};let curMethod='card',PAY_GEN=0;\n"
          + money.group(0) + "\n" + fn +
          "\nvar ORDER={payable:true,paylink_enabled:true,total_cents:47300,"
          "pay_amount_cents:4000,balance_due_cents:4000};setupPay();"
          "console.log(JSON.stringify([$('pay-btn').textContent,$('pay-due').innerHTML]));")
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True,
                                    check=True).stdout)
    assert "40.00" in out[0] and "473" not in out[0] and "40.00" in out[1]


def test_paying_past_the_balance_is_recorded_and_glen_told(env):
    oid, _tok = _like_230(env)
    _return(env, oid, pi="pi_tab_one")
    assert env["sent"] == []
    _return(env, oid, pi="pi_tab_two")      # a second tab, paid too
    _return(env, oid, pi="pi_tab_two")      # and its reload tells no one twice
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == -4000
    assert len(env["sent"]) == 1 and "refund" in env["sent"][0][2]


def test_the_backfill_writes_once(env):
    oid, _tok = _like_230(env)
    assert env["OP"].backfill_legacy_payment(env["cx"], oid) is True
    assert env["OP"].backfill_legacy_payment(env["cx"], oid) is False
    assert len(env["OP"].list_payments(env["cx"], oid)) == 1


def test_a_refund_is_never_asked_for_again(env):
    oid, tok = env["order"](ref="DS-R", total=47300, pay_status="paid", paid_cents=47300,
                            pay_method="card", paid_at="2026-10-01T00:00:00Z")
    env["mp"].setattr(env["OP"].stripe_pay, "refund", lambda *a, **k: {"id": "re_1"})
    pay = env["OP"].add_payment(env["cx"], oid, 47300, "card", source="manual")
    env["OP"].add_refund(env["cx"], oid, 4000, "card", refunds_payment_id=pay["id"])
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 4000
    o = env["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["payable"] is False
    assert env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"}).status_code == 409


def test_rae_recording_the_zelle_balance_keeps_the_earlier_payment(env):
    oid, tok = _like_230(env)
    env["OP"].add_payment(env["cx"], oid, 4000, "Zelle", source="manual")
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0
    assert env["c"].get(f"/api/invoice/{tok}").get_json()["order"]["payable"] is False


def test_the_original_card_payment_recorded_later_is_not_counted_twice(env):
    oid, _tok = _like_230(env)
    env["OP"].add_payment(env["cx"], oid, 43300, "card", source="stripe",
                          external_ref="pi_original")
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 4000


def test_the_original_payment_intent_alone_marks_the_same_money(env):
    oid, _tok = _like_230(env)
    env["OP"].add_payment(env["cx"], oid, 43000, "card", source="stripe",
                          external_ref="pi_original")
    assert [r["source"] for r in env["OP"].list_payments(env["cx"], oid)] == ["stripe"]


def test_a_balance_equal_to_the_earlier_payment_is_new_money(env):
    oid, tok = env["order"](ref="DS-EQ", total=40000, pay_status="paid", paid_cents=20000,
                            pay_method="card", paid_at="2026-10-01T00:00:00Z",
                            stripe_payment_intent="pi_first")
    _return(env, oid, pi="pi_second", amount=20000)
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0
    assert env["c"].get(f"/api/invoice/{tok}").get_json()["order"]["payable"] is False


def test_an_unpaid_shipped_order_never_takes_the_path_that_reopens_it(env):
    oid, tok = env["order"](ref="DS-SHIP", total=9000, status="shipped", source="in-house")
    env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    md = env["made"][-1]["metadata"]
    assert md["kind"] == "invoice-balance" and "invoice_id" not in md
    _return(env, oid, pi="pi_ship", amount=9000)
    o = env["O"].get_order(env["cx"], oid)
    assert (o["status"], o["pay_status"]) == ("shipped", "paid")


def test_the_webhook_ignores_other_kinds(env):
    oid, _tok = _like_230(env)
    env["mp"].setattr(env["stripe"], "get_session", lambda sid: {
        "id": sid, "payment_status": "paid", "amount_total": 4000,
        "payment_intent": "pi_other",
        "metadata": {"kind": "caregiver-pay", "order_id": str(oid)}})
    env["app"]._fulfill_invoice_balance("cs_test_3")
    assert env["OP"].list_payments(env["cx"], oid) == []


def test_a_fully_paid_legacy_order_gets_no_backfill(env):
    oid, _tok = env["order"](ref="DS-FULL", total=43300, pay_status="paid",
                             paid_cents=43300, pay_method="card")
    assert env["OP"].backfill_legacy_payment(env["cx"], oid) is False


def test_a_cancelled_or_unpaid_order_gets_no_backfill(env):
    a, _ = env["order"](ref="DS-C", total=47300, status="cancelled", pay_status="paid",
                        paid_cents=43300)
    b, _ = env["order"](ref="DS-U", total=47300, pay_status="unpaid", paid_cents=43300)
    assert env["OP"].backfill_legacy_payment(env["cx"], a) is False
    assert env["OP"].backfill_legacy_payment(env["cx"], b) is False


def test_the_stripe_webhook_calls_the_balance_fulfiller():
    import inspect
    app_module = importlib.import_module("app")
    import ast, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(app_module.webhook_stripe)))
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_fulfill_invoice_balance" in calls


def test_a_fully_paid_legacy_order_reconciled_by_hand_is_not_counted_twice(env):
    oid, _tok = env["order"](ref="DS-F", total=43300, pay_status="paid", paid_cents=43300,
                             pay_method="Zelle", paid_at="2026-10-01T00:00:00Z")
    env["OP"].add_payment(env["cx"], oid, 43300, "Zelle", source="manual")
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0


def test_a_voided_row_does_not_stop_the_backfill(env):
    oid, _tok = _like_230(env)
    env["cx"].execute("INSERT INTO order_payments (order_id, kind, amount_cents, method, "
                      "source, status, created_at) VALUES (?, 'payment', 100, 'Zelle', "
                      "'manual', 'voided', '2026-10-17')", (oid,))
    env["cx"].commit()
    assert env["OP"].balance(env["cx"], oid)["legacy_fallback"] is True
    _return(env, oid)
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0


def test_a_closed_tab_is_recorded_by_the_webhook(env):
    oid, _tok = _like_230(env)
    env["mp"].setattr(env["stripe"], "get_session", lambda sid: {
        "id": sid, "payment_status": "paid", "amount_total": 4000,
        "payment_intent": "pi_webhook",
        "metadata": {"kind": "invoice-balance", "order_id": str(oid)}})
    env["app"]._fulfill_invoice_balance("cs_test_2")
    env["app"]._fulfill_invoice_balance("cs_test_2")
    assert env["OP"].balance(env["cx"], oid)["balance_cents"] == 0
    assert len(env["OP"].list_payments(env["cx"], oid)) == 2


def test_a_balance_card_payment_shows_in_the_money_view_once(env):
    oid, _tok = _like_230(env)
    _return(env, oid)
    shown = [r for r in env["OP"].ledger_rows_for_payments_view(env["cx"])
             if r["amount_cents"] in (4000, 43300)]
    assert [(r["amount_cents"], r["source"]) for r in shown] == [(4000, "card:balance")]


def test_a_claim_with_no_balance_reading_sends_no_amount(env):
    _oid, tok = _like_230(env)
    env["mp"].setattr(env["app"], "_invoice_balance_cents", lambda o: None)
    r = env["c"].post(f"/api/invoice/{tok}/claim-paid", json={"method": "zelle"})
    assert r.status_code == 503 and env["sent"] == []


def test_the_receipt_names_the_order(env):
    oid, _tok = _like_230(env)
    _return(env, oid)
    html = env["c"].get("/order-confirmation?session_id=cs_test_1").get_data(as_text=True)
    assert f"#{oid}</strong>" in html and "$40.00" in html


def test_pay_with_no_balance_reading_charges_nothing(env):
    _oid, tok = _like_230(env)
    env["mp"].setattr(env["app"], "_invoice_balance_cents", lambda o: None)
    r = env["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 503 and env["made"] == []
