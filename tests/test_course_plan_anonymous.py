"""A $297x12 plan bought signed out: the sales page button posts no email, so the
subscription metadata carries none. The buyer's email arrives only through
checkout.session.completed, which stores it on a kind='plan' entitlement row.
Every later invoice.paid must still find that buyer: extend access each month,
and on the 12th charge grant the lifetime cert and cancel the subscription.
"""
import sqlite3

import pytest


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    # Do NOT importlib.reload(app): see tests/test_courses_webhook.py for why.
    import app as m
    monkeypatch.setattr(m, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(m, "send_mentorship_setup_link", lambda *a, **k: ("test", None))
    monkeypatch.setattr(m, "_autoprovision_course_access", lambda email: None)
    monkeypatch.setattr(m, "_send_token_alert", lambda *a, **k: None)
    m.app.config["TESTING"] = True
    return m


def _sub(cpe):
    # Anonymous checkout: metadata has kind but email is None.
    return {"id": "sub_anon", "status": "active", "current_period_end": cpe,
            "customer": "cus_anon", "metadata": {"kind": "course_plan", "email": None}}


def _rows(appmod):
    cx = sqlite3.connect(str(appmod.LOG_DB))
    try:
        return cx.execute("SELECT kind, status, email, expires_at FROM course_entitlements "
                          "ORDER BY id").fetchall()
    finally:
        cx.close()


def _buy_signed_out(appmod, monkeypatch, cpe):
    from dashboard import stripe_pay
    monkeypatch.setattr(stripe_pay, "get_subscription", lambda sid: _sub(cpe))
    session = {"id": "cs_anon", "mode": "subscription", "subscription": "sub_anon",
               "customer": "cus_anon", "customer_details": {"email": "buyer@x.com"},
               "metadata": {"kind": "course_purchase", "email": None, "product": "plan"}}
    assert appmod._fulfill_course_purchase(session) == "ok"


def test_signed_out_plan_renewal_extends_access(appmod, monkeypatch):
    _buy_signed_out(appmod, monkeypatch, cpe=1_000_000_000)
    from dashboard import stripe_pay
    monkeypatch.setattr(stripe_pay, "get_subscription", lambda sid: _sub(2_000_000_000))
    appmod._course_plan_charge({"subscription": "sub_anon", "id": "in_1", "amount_paid": 29700})
    appmod._course_plan_charge({"subscription": "sub_anon", "id": "in_2", "amount_paid": 29700})
    plan = [r for r in _rows(appmod) if r[0] == "plan"]
    assert len(plan) == 1 and plan[0][2] == "buyer@x.com"
    assert float(plan[0][3]) == 2_000_000_000, plan


def test_signed_out_plan_converts_and_cancels_at_twelve(appmod, monkeypatch):
    _buy_signed_out(appmod, monkeypatch, cpe=1_000_000_000)
    from dashboard import stripe_pay
    cancelled = []
    monkeypatch.setattr(stripe_pay, "cancel_subscription", lambda sid: cancelled.append(sid))
    for i in range(1, 13):
        appmod._course_plan_charge({"subscription": "sub_anon", "id": f"in_{i}",
                                    "amount_paid": 29700})
    kinds = {(r[0], r[1], r[2]) for r in _rows(appmod)}
    assert ("cert_onetime", "active", "buyer@x.com") in kinds, kinds
    assert cancelled == ["sub_anon"]
