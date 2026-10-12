"""Clients earn 3.5% of what they pay by Zelle or Wise, as loyalty points.

Glen, 2026-10-10: the fee-free credit goes to clients as "points", on the amount paid, when
the payment arrives, on the store cart, the portal and invoices. Those points redeem on
services only, like own-purchase points. Glen, 2026-09-10: a mailed check does not earn
it. Glen, 2026-10-11: "courtesy earns it". Practitioner sales are left to the practitioner
wallet's own fee-free credit.
"""
import sqlite3

import pytest

appmod = pytest.importorskip("app")
from dashboard import order_payments as op
from dashboard import orders, points, referrals as rf, rewards


@pytest.fixture
def cx(tmp_path, monkeypatch):
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    orders.init_orders_table(c)
    op.ensure_table(c)
    points.init_points_table(c)
    rewards.init_affiliate_earnings_table(c)
    rf.init_tables(c)
    c.execute("CREATE TABLE people (email TEXT UNIQUE, tags TEXT DEFAULT '[]')")
    c.execute("CREATE TABLE affiliate_signups (slug TEXT UNIQUE, email TEXT, status TEXT)")
    c.execute("CREATE TABLE referral_events (received_at TEXT, email TEXT, utm_source TEXT)")
    c.commit()
    monkeypatch.setattr(appmod._pp, "modules_completed_for_email", lambda e: None)
    monkeypatch.setattr(appmod, "_REFERRALS", False)
    return c


def _order(cx, *, ref="INH-FF1", total=10000, source="in-house", status="proposed"):
    oid = orders.upsert_order(cx, source=source, external_ref=ref, email="Buyer@x.com",
                              total_cents=total,
                              items=[{"slug": "liver-support", "qty": 1,
                                      "unit_cents": total, "line_cents": total}])
    cx.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    cx.commit()
    return oid


def _fee_free(cx, ref):
    return [(r["email"], r["delta_cents"]) for r in cx.execute(
        "SELECT email, delta_cents FROM points_ledger WHERE reason=? AND order_ref=?",
        (points.FEE_FREE_REASON, ref)).fetchall()]


def test_the_rate_is_three_and_a_half_percent():
    assert points.FEE_FREE_PCT == 0.035


def test_a_zelle_payment_earns_three_and_a_half_percent(cx):
    oid = _order(cx)
    op.add_payment(cx, oid, 10000, "Zelle")
    assert orders.get_order(cx, oid)["pay_status"] == "paid"
    assert _fee_free(cx, "INH-FF1") == [("buyer@x.com", 350)]


def test_a_wise_payment_earns_it(cx):
    oid = _order(cx, ref="INH-FF2")
    op.add_payment(cx, oid, 10000, "Wise")
    assert _fee_free(cx, "INH-FF2") == [("buyer@x.com", 350)]


@pytest.mark.parametrize("method", ["Check", "card", "Credit card (Stripe)", "cash"])
def test_other_methods_earn_nothing(cx, method):
    oid = _order(cx, ref="INH-FF3")
    op.add_payment(cx, oid, 10000, method)
    assert _fee_free(cx, "INH-FF3") == []


def test_only_the_zelle_part_of_a_split_payment_counts(cx):
    oid = _order(cx, ref="INH-FF4")
    op.add_payment(cx, oid, 4000, "Zelle")
    assert _fee_free(cx, "INH-FF4") == []          # not paid yet
    op.add_payment(cx, oid, 6000, "Check")
    assert _fee_free(cx, "INH-FF4") == [("buyer@x.com", 140)]


def test_shipping_is_part_of_what_was_paid(cx):
    oid = _order(cx, ref="INH-FF5", total=12295)
    cx.execute("UPDATE orders SET shipping_cents=2295 WHERE id=?", (oid,))
    cx.commit()
    op.add_payment(cx, oid, 12295, "Zelle")
    assert _fee_free(cx, "INH-FF5") == [("buyer@x.com", 430)]   # 3.5% of $122.95


@pytest.mark.parametrize("source", ["dropship", "dispensary", "personal", "wholesale"])
def test_practitioner_sales_are_left_to_the_wallet(cx, source):
    oid = _order(cx, ref="DS-FF6", source=source)
    op.add_payment(cx, oid, 10000, "Zelle")
    assert _fee_free(cx, "DS-FF6") == []


@pytest.mark.parametrize("source", ["in-house", "funnel", "portal-reorder", "cart"])
def test_client_orders_from_every_checkout_earn_it(cx, source):
    oid = _order(cx, ref="C-FF7", source=source, status="new" if source != "in-house" else "proposed")
    op.add_payment(cx, oid, 10000, "Zelle")
    assert _fee_free(cx, "C-FF7") == [("buyer@x.com", 350)]


def test_a_courtesy_price_still_earns_it(cx, monkeypatch):
    # Glen, 2026-10-11: "courtesy earns it". Purchase points stay off for such an order.
    monkeypatch.setattr(appmod, "_order_has_line_below_store_price", lambda o: True)
    monkeypatch.setattr(orders, "_below_store_price_check", lambda o: True)
    oid = _order(cx, ref="INH-FF8")
    op.add_payment(cx, oid, 10000, "Zelle")
    assert _fee_free(cx, "INH-FF8") == [("buyer@x.com", 350)]
    assert cx.execute("SELECT COUNT(*) FROM points_ledger WHERE reason='earn'").fetchone()[0] == 0


def test_the_record_payment_action_earns_it_once(cx):
    oid = _order(cx, ref="INH-FF9")
    orders._record_payment_exec({"order_id": oid, "method": "Zelle", "amount_cents": 10000},
                                {"cx": cx})
    assert _fee_free(cx, "INH-FF9") == [("buyer@x.com", 350)]
    orders.credit_fee_free_on_payment(cx, orders.get_order(cx, oid))
    assert _fee_free(cx, "INH-FF9") == [("buyer@x.com", 350)]


def test_the_record_payment_action_by_check_earns_nothing(cx):
    oid = _order(cx, ref="INH-FF10")
    orders._record_payment_exec({"order_id": oid, "method": "check", "amount_cents": 10000},
                                {"cx": cx})
    assert _fee_free(cx, "INH-FF10") == []


def test_fee_free_points_redeem_on_services_only(cx):
    points.credit(cx, "buyer@x.com", value_cents=350, reason=points.FEE_FREE_REASON,
                  order_ref="INH-FF11")
    points.credit(cx, "buyer@x.com", value_cents=200, reason="referral_reward",
                  order_ref="referral:f@x.com")
    assert points.product_usable_credits(cx, "buyer@x.com") == 200


# The client wording Glen approved on 2026-10-11, and every page that carries it.
_ZELLE = ("Pay by Zelle and {pct}% of your payment comes back to you as loyalty points, "
          "for use toward services. The points are added when your payment arrives.")
_ZELLE_WISE = _ZELLE.replace("Pay by Zelle and", "Pay by Zelle or Wise and")
_PAGES = {"static/begin-cart.html": (_ZELLE, 1), "static/client-portal.html": (_ZELLE, 2),
          "static/invoice.html": (_ZELLE_WISE, 1), "static/begin-buy.html": (_ZELLE_WISE, 1)}


@pytest.mark.parametrize("page", sorted(_PAGES))
def test_each_page_states_the_rate_the_code_pays(page):
    from pathlib import Path
    text, n = _PAGES[page]
    pct = f"{points.FEE_FREE_PCT * 100:g}"
    html = (Path(__file__).resolve().parent.parent / page).read_text()
    assert html.count(text.format(pct=pct)) == n


@pytest.mark.parametrize("source,shown", [("in-house", True), ("cart", True),
                                          ("dropship", False), ("dispensary", False)])
def test_the_invoice_shows_the_note_only_where_it_applies(cx, source, shown):
    oid = _order(cx, ref="INV-FF12", source=source)
    assert appmod._invoice_summary(orders.get_order(cx, oid))["fee_free_points"] is shown


def test_record_payment_by_zelle_after_a_partial_ledger_payment_earns_on_its_part(cx):
    # Review rounds 1 and 2: a $60 check in the ledger, then Record payment by Zelle.
    oid = _order(cx, ref="INH-FF13")
    op.add_payment(cx, oid, 6000, "Check")
    orders._record_payment_exec({"order_id": oid, "method": "Zelle", "amount_cents": 4000},
                                {"cx": cx})
    assert _fee_free(cx, "INH-FF13") == [("buyer@x.com", 140)]


def test_an_overpayment_earns_only_on_the_order_total(cx):
    oid = _order(cx, ref="INH-FF14")
    op.add_payment(cx, oid, 20000, "Zelle")
    assert _fee_free(cx, "INH-FF14") == [("buyer@x.com", 350)]


def test_a_failed_credit_leaves_the_connection_usable(cx, monkeypatch):
    # Review rounds 1 and 2: on Postgres a failed statement aborts the transaction.
    rolled = []
    monkeypatch.setattr(orders, "_safe_rollback", lambda c: rolled.append(1))
    monkeypatch.setattr(points, "credit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    oid = _order(cx, ref="INH-FF15")
    cx.execute("UPDATE orders SET pay_method='Zelle', paid_cents=10000 WHERE id=?", (oid,))
    cx.commit()
    assert orders.credit_fee_free_on_payment(cx, orders.get_order(cx, oid)) == 0
    assert rolled == [1]


def test_the_console_settle_route_reruns_a_missed_credit(cx, monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "k")
    oid = _order(cx, ref="INH-FF16")
    cx.execute("UPDATE orders SET pay_status='paid', status='done', pay_method='Zelle', "
               "paid_cents=10000 WHERE id=?", (oid,))
    cx.commit()
    r = appmod.app.test_client().post(f"/api/console/orders/{oid}/settle-points?apply=1",
                                      headers={"X-Console-Key": "k"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["fee_free_cents"] == 350
    assert _fee_free(cx, "INH-FF16") == [("buyer@x.com", 350)]


def test_the_invoice_note_starts_hidden():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "static/invoice.html").read_text()
    assert '<p class="muted fee-free-note" hidden' in html
    assert "el.hidden = t.fee_free_points !== true;" in html
