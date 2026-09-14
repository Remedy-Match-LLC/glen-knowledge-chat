"""Buyer points on orders the payment ledger marks paid, and the backfill route.

The payments panel and the Zelle import record payments through
order_payments.add_payment. When the ledger reached the invoice total it marked the
order paid but settled no buyer points, unlike the card path and the Record payment
action. 11 orders were short on 2026-09-14; Glen approved paying them and said store
orders should earn too.
"""
import json
import sqlite3

import pytest

appmod = pytest.importorskip("app")
from dashboard import order_payments as op
from dashboard import orders, points, referrals as rf, rewards

KEY = "testkey"


@pytest.fixture
def cx(tmp_path, monkeypatch):
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", KEY)
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
    monkeypatch.setenv("REWARDS_TIERS_ENABLED", "true")
    monkeypatch.setattr(appmod._pp, "modules_completed_for_email", lambda e: None)
    monkeypatch.setattr(appmod, "_REFERRALS", False)
    return c


def _order(cx, *, email="buyer@x.com", ref="INH-PTS1", total=6000, source="in-house",
           status="proposed"):
    oid = orders.upsert_order(cx, source=source, external_ref=ref, email=email,
                              total_cents=total,
                              items=[{"slug": "liver-support", "qty": 1,
                                      "unit_cents": total, "line_cents": total}])
    cx.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    cx.commit()
    return oid


def _expected(total):
    pct = float(appmod._pricing_settings().get("points_earn_pct", 0.05))
    return int(round(total * pct))


def _earn_rows(cx, ref):
    return cx.execute("SELECT email, delta_cents FROM points_ledger WHERE reason='earn' "
                      "AND order_ref=?", (ref,)).fetchall()


def _spy(monkeypatch):
    real, seen = orders._points_settle_hook, []

    def spy(c, o):
        seen.append(o["id"])
        return real(c, o)

    monkeypatch.setattr(orders, "_points_settle_hook", spy)
    return seen


def test_points_hook_is_registered_when_app_imports():
    assert orders._points_settle_hook is not None


def test_a_ledger_payment_earns_the_buyer_points(cx):
    oid = _order(cx)
    op.add_payment(cx, oid, 6000, "Zelle")
    assert orders.get_order(cx, oid)["pay_status"] == "paid"
    assert _expected(6000) > 0
    assert [(r["email"], r["delta_cents"]) for r in _earn_rows(cx, "INH-PTS1")] == \
        [("buyer@x.com", _expected(6000))]


def test_a_partial_payment_earns_nothing_until_fully_paid(cx):
    oid = _order(cx, ref="INH-PTS2")
    op.add_payment(cx, oid, 2000, "Zelle")
    assert _earn_rows(cx, "INH-PTS2") == []
    op.add_payment(cx, oid, 4000, "Check")
    assert len(_earn_rows(cx, "INH-PTS2")) == 1


def test_a_store_order_recorded_by_hand_earns_points(cx):
    oid = _order(cx, ref="1099", source="groovekart", status="new")
    op.add_payment(cx, oid, 6000, "card")
    assert points.balance(cx, "buyer@x.com") == _expected(6000)


def test_a_wholesale_order_earns_no_points(cx, monkeypatch):
    oid = _order(cx, ref="DS-PTS3", source="dropship")
    seen = _spy(monkeypatch)
    op.add_payment(cx, oid, 6000, "Zelle")
    assert seen == [oid]            # the settler ran and declined
    assert _earn_rows(cx, "DS-PTS3") == []


def test_points_already_earned_by_the_record_payment_action_are_not_doubled(cx):
    oid = _order(cx, ref="INH-PTS4")
    orders.settle_order_points(cx, orders.get_order(cx, oid))   # the action path's settler
    assert len(_earn_rows(cx, "INH-PTS4")) == 1
    op.add_payment(cx, oid, 6000, "Zelle")
    assert len(_earn_rows(cx, "INH-PTS4")) == 1


def _post(path):
    return appmod.app.test_client().post(path, headers={"X-Console-Key": KEY})


def _paid_without_points(cx, ref="INH-OLD1", source="in-house"):
    oid = _order(cx, ref=ref, source=source, status="done")
    cx.execute("UPDATE orders SET pay_status='paid', paid_cents=6000 WHERE id=?", (oid,))
    cx.commit()
    return oid


def test_backfill_preview_writes_nothing(cx):
    oid = _paid_without_points(cx)
    r = _post(f"/api/console/orders/{oid}/settle-points")
    body = r.get_json()
    assert r.status_code == 200 and body["applied"] is False
    assert body["already_earned"] is False
    assert _earn_rows(cx, "INH-OLD1") == []


def test_backfill_apply_credits_once(cx):
    oid = _paid_without_points(cx)
    first = _post(f"/api/console/orders/{oid}/settle-points?apply=1").get_json()
    assert first["applied"] is True and first["credited_cents"] == _expected(6000)
    second = _post(f"/api/console/orders/{oid}/settle-points?apply=1").get_json()
    assert second["already_earned"] is True and second["credited_cents"] == 0
    assert len(_earn_rows(cx, "INH-OLD1")) == 1


def test_backfill_refuses_an_unpaid_order(cx):
    oid = _order(cx, ref="INH-UNPAID")
    r = _post(f"/api/console/orders/{oid}/settle-points?apply=1")
    assert r.status_code == 409
    assert _earn_rows(cx, "INH-UNPAID") == []


def test_backfill_requires_the_console_key(cx):
    oid = _paid_without_points(cx, ref="INH-KEY")
    r = appmod.app.test_client().post(f"/api/console/orders/{oid}/settle-points?apply=1",
                                      headers={"X-Console-Key": "wrong"})
    assert r.status_code == 401
    assert _earn_rows(cx, "INH-KEY") == []
