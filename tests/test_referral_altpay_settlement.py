"""Referral rewards on orders paid outside Stripe.

The card path credits referrers through the settlement hub. An order paid by Zelle,
cheque, cash or an owner-recorded payment used to reach neither settler, so the
referrer was never paid. These tests drive the two real non-card entry points:

  * order_payments.add_payment, which projects the order paid when the ledger
    reaches the invoice total (console payments panel, Zelle email import);
  * orders._record_payment_exec, the "Record payment" order action.
"""
import json
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
    # Attribution tables, in the shape the attribution settler's own tests use.
    c.execute("CREATE TABLE people (email TEXT UNIQUE, tags TEXT DEFAULT '[]')")
    c.execute("CREATE TABLE affiliate_signups (slug TEXT UNIQUE, email TEXT, status TEXT)")
    c.execute("CREATE TABLE referral_events (received_at TEXT, email TEXT, utm_source TEXT)")
    c.execute("""CREATE TABLE todos (id INTEGER PRIMARY KEY, created_at TEXT, owner TEXT,
                 category TEXT, title TEXT, body TEXT, priority TEXT, status TEXT DEFAULT 'open',
                 source TEXT, dedup_key TEXT UNIQUE)""")
    c.commit()
    monkeypatch.setenv("REWARDS_TIERS_ENABLED", "true")
    monkeypatch.setattr(appmod._pp, "modules_completed_for_email", lambda e: None)
    # Code referrals off unless a test turns them on.
    monkeypatch.setattr(appmod, "_REFERRALS", False)
    return c


def _refer(cx, buyer, slug="doc", ref_email="doc@x.com"):
    cx.execute("INSERT INTO affiliate_signups VALUES (?,?,?)", (slug, ref_email, "approved"))
    cx.execute("INSERT INTO people (email, tags) VALUES (?,?)",
               (ref_email, json.dumps(["type:practitioner"])))
    cx.execute("INSERT INTO referral_events VALUES ('2026-01-01', ?, ?)", (buyer, slug))
    cx.commit()


def _order(cx, *, email="buyer@x.com", ref="INH-REF1", total=6000, source="in-house",
           status="proposed"):
    oid = orders.upsert_order(cx, source=source, external_ref=ref, email=email,
                              total_cents=total,
                              items=[{"slug": "liver-support", "qty": 1,
                                      "unit_cents": total, "line_cents": total}])
    cx.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    cx.commit()
    return oid


def _expected_attribution(total, ref_email="doc@x.com"):
    settings = rewards.load_settings(appmod._rewards_settings())
    return round(total * appmod._referral_pct_for_referrer(ref_email, settings))


def _referral_rows(cx, reason):
    return cx.execute("SELECT email, delta_cents, order_ref FROM points_ledger WHERE reason=?",
                      (reason,)).fetchall()


def test_hook_is_registered_when_app_imports():
    assert orders._referral_settle_hook is not None


def test_zelle_ledger_payment_credits_the_attribution_referrer(cx):
    _refer(cx, "buyer@x.com")
    oid = _order(cx)
    op.add_payment(cx, oid, 6000, "Zelle")
    assert orders.get_order(cx, oid)["pay_status"] == "paid"   # the transition happened
    expected = _expected_attribution(6000)
    assert expected > 0
    assert points.balance(cx, "doc@x.com") == expected
    rows = _referral_rows(cx, "referral")
    assert [(r["email"], r["order_ref"]) for r in rows] == [("doc@x.com", "INH-REF1")]


def test_partial_payment_credits_nothing_until_the_order_is_fully_paid(cx):
    _refer(cx, "buyer@x.com")
    oid = _order(cx)
    op.add_payment(cx, oid, 2500, "Zelle")
    assert orders.get_order(cx, oid)["pay_status"] != "paid"
    assert points.balance(cx, "doc@x.com") == 0
    op.add_payment(cx, oid, 3500, "Check")
    assert points.balance(cx, "doc@x.com") == _expected_attribution(6000)


def test_record_payment_action_credits_the_attribution_referrer(cx):
    _refer(cx, "buyer@x.com")
    oid = _order(cx, ref="INH-REF2")
    res = orders._record_payment_exec({"order_id": oid, "method": "Cash"}, {"cx": cx})
    assert res["pay_status"] == "paid"
    assert points.balance(cx, "doc@x.com") == _expected_attribution(6000)
    assert [r["order_ref"] for r in _referral_rows(cx, "referral")] == ["INH-REF2"]


def test_referral_code_reward_is_paid_on_a_non_card_order(cx, monkeypatch):
    monkeypatch.setattr(appmod, "_REFERRALS", True)
    monkeypatch.setattr(appmod, "REFERRAL_TIER2_ENABLED", False)
    monkeypatch.setattr(appmod, "_referrer_reward_pct", lambda: 10)
    code = rf.get_or_create_code(cx, "owner@x.com")
    rf.record_redemption(cx, code, "owner@x.com", "friend@x.com", "INH-CODE1")
    oid = _order(cx, email="friend@x.com", ref="INH-CODE1", total=10000)
    op.add_payment(cx, oid, 10000, "Zelle")
    assert points.balance(cx, "owner@x.com") == 1000
    red = rf.redemption_by_order_ref(cx, "INH-CODE1")
    assert red["rewarded_at"] and red["reward_cents"] == 1000


def _spy_on_hook(monkeypatch):
    """Wrap the registered hook so a test can prove it RAN and declined, rather
    than passing because nothing called it."""
    real, seen = orders._referral_settle_hook, []

    def spy(c, o):
        seen.append(o["id"])
        return real(c, o)

    monkeypatch.setattr(orders, "_referral_settle_hook", spy)
    return seen


def test_an_order_already_settled_by_card_is_not_paid_twice(cx, monkeypatch):
    _refer(cx, "buyer@x.com")
    oid = _order(cx, ref="INH-REF3")
    appmod._settle_referral(orders.get_order(cx, oid), order_ref="INH-REF3")   # card hub
    first = points.balance(cx, "doc@x.com")
    assert first == _expected_attribution(6000)
    seen = _spy_on_hook(monkeypatch)
    op.add_payment(cx, oid, 6000, "Zelle")
    assert seen == [oid]
    assert points.balance(cx, "doc@x.com") == first
    assert len(_referral_rows(cx, "referral")) == 1


def test_a_wholesale_order_credits_no_referrer(cx, monkeypatch):
    _refer(cx, "buyer@x.com")
    oid = _order(cx, ref="DS-REF4", source="dropship")
    seen = _spy_on_hook(monkeypatch)
    op.add_payment(cx, oid, 6000, "Zelle")
    assert orders.get_order(cx, oid)["pay_status"] == "paid"
    assert seen == [oid]
    assert points.balance(cx, "doc@x.com") == 0


def test_a_failing_referral_hook_never_blocks_the_payment(cx, monkeypatch):
    calls = []

    def boom(_cx, _o):
        calls.append(_o["id"])
        raise RuntimeError("referral store down")

    monkeypatch.setattr(orders, "_referral_settle_hook", boom)
    oid = _order(cx, ref="INH-REF5")
    op.add_payment(cx, oid, 6000, "Zelle")
    assert calls == [oid]   # the hook was reached, not skipped before it
    assert orders.get_order(cx, oid)["pay_status"] == "paid"
