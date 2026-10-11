"""Glen, 2026-10-10: "put the credit on hold while payment is pending so it can't be used
again during that time."

Ship credit and redeemed points are debited only when an order is paid. A Zelle order can
stay unpaid for days, so before this a second order placed meanwhile planned the same
balance again, and the customer got the credit twice. Now credit planned on an unpaid,
uncancelled order is held and not offered to a new one.
"""
import sqlite3

import pytest

import app
import dashboard.orders as O
from dashboard import points, ship_credit

EMAIL = "held@x.com"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    cx = sqlite3.connect(path)
    O.init_orders_table(cx)
    points.init_points_table(cx)
    ship_credit.grant(cx, EMAIL, 1500, source_ref="overpaid-1")
    points.earn(cx, EMAIL, full_price_cents=20000, earn_pct=0.05, order_ref="seed")  # 1000
    cx.commit(); cx.close()
    return path


def _order(db, ref, *, ship_credit_cents=0, points_cents=0, pay_status=None, status="new",
           email=EMAIL):
    cx = sqlite3.connect(db)
    oid = O.upsert_order(cx, source="reorder", external_ref=ref, email=email,
                         total_cents=5000, items=[],
                         points_redeemed_cents=points_cents,
                         ship_credit_applied_cents=ship_credit_cents)
    if pay_status:
        cx.execute("UPDATE orders SET pay_status=? WHERE id=?", (pay_status, oid))
    cx.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    cx.commit(); cx.close()
    return oid


def test_without_unpaid_orders_the_whole_balance_is_offered(db):
    assert app._plan_ship_credit(EMAIL, 10000) == 1500


def test_credit_on_an_unpaid_order_is_held(db):
    _order(db, "Z1", ship_credit_cents=1000)
    assert app._plan_ship_credit(EMAIL, 10000) == 500


def test_all_of_it_held_offers_nothing(db):
    _order(db, "Z1", ship_credit_cents=1500)
    assert app._plan_ship_credit(EMAIL, 10000) == 0


def test_a_paid_or_cancelled_order_holds_nothing(db):
    _order(db, "P1", ship_credit_cents=1000, pay_status="paid")
    _order(db, "C1", ship_credit_cents=1000, status="cancelled")
    assert app._plan_ship_credit(EMAIL, 10000) == 1500


def test_another_customers_order_holds_nothing(db):
    _order(db, "O1", ship_credit_cents=1500, email="other@x.com")
    assert app._plan_ship_credit(EMAIL.upper(), 10000) == 1500


def test_points_on_an_unpaid_order_are_held_at_checkout(db, monkeypatch):
    _order(db, "Z2", points_cents=700)
    seen = {}

    def fake_price_cart(cart, **kw):
        seen["redeem"] = kw.get("points_to_redeem_cents")
        raise app.CheckoutError("stop here")
    monkeypatch.setattr(app, "_price_cart", fake_price_cart)
    monkeypatch.setattr(app, "_resolve_checkout_coupon_pct", lambda code, email: (None, None))
    with pytest.raises(app.CheckoutError):
        app._checkout_cart(EMAIL, [{"slug": "x", "qty": 1}], ship={}, points_to_redeem_cents=1000)
    assert seen["redeem"] == 300


def test_points_fully_held_redeem_nothing(db, monkeypatch):
    _order(db, "Z3", points_cents=1000)
    seen = {}

    def fake_price_cart(cart, **kw):
        seen["redeem"] = kw.get("points_to_redeem_cents")
        raise app.CheckoutError("stop here")
    monkeypatch.setattr(app, "_price_cart", fake_price_cart)
    monkeypatch.setattr(app, "_resolve_checkout_coupon_pct", lambda code, email: (None, None))
    with pytest.raises(app.CheckoutError):
        app._checkout_cart(EMAIL, [{"slug": "x", "qty": 1}], ship={}, points_to_redeem_cents=500)
    assert seen["redeem"] == 0
