"""A store-cart order that owes nothing is saved as paid. Being paid releases the hold on
the ship credit and points that cover it, so they must be spent at that moment, or the
same credit could pay for a second order (found in review of #1980, 2026-10-12)."""
import sqlite3

import app
from dashboard import points as P
from dashboard import ship_credit
from tests.test_cart_zelle_checkout import ADDRESS, EMAIL, _orders, env  # noqa: F401


def _seed_ship_credit(db, cents):
    cx = sqlite3.connect(db)
    try:
        app._points_init_ship(cx)
        ship_credit.grant(cx, EMAIL, cents, source_ref="seed")
        cx.commit()
    finally:
        cx.close()


def _ship_balance(db):
    cx = sqlite3.connect(db)
    try:
        return ship_credit.balance(cx, EMAIL)
    finally:
        cx.close()


def test_a_cart_covered_by_ship_credit_spends_it(env, monkeypatch):
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    _seed_ship_credit(env["db"], 20000)
    r = env["client"].post("/api/cart/checkout", json={"address": ADDRESS})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert env["stripe_calls"] == []
    order = _orders(env["db"])[0]
    assert order["pay_status"] == "paid"
    assert order["total_cents"] == 0
    applied = int(order["ship_credit_applied_cents"])
    assert applied > 0
    assert _ship_balance(env["db"]) == 20000 - applied


def test_a_second_cart_cannot_reuse_the_spent_credit(env, monkeypatch):
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    _seed_ship_credit(env["db"], 20000)
    assert env["client"].post("/api/cart/checkout", json={"address": ADDRESS}).status_code == 200
    first = int(_orders(env["db"])[0]["ship_credit_applied_cents"])
    assert app._plan_ship_credit(EMAIL, 50000) == 20000 - first


def test_a_cart_paid_with_points_and_ship_credit_debits_both(env, monkeypatch):
    # Points cover products only, so ship credit covers the shipping.
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    _seed_ship_credit(env["db"], 5000)
    cx = sqlite3.connect(env["db"])
    try:
        P.init_points_table(cx)
        P.credit(cx, EMAIL, value_cents=20000, reason="referral", order_ref="seed")
        cx.commit()
        before = P.balance(cx, EMAIL)
    finally:
        cx.close()
    r = env["client"].post("/api/cart/checkout",
                           json={"address": ADDRESS, "points_to_redeem_cents": 20000})
    assert r.status_code == 200, r.get_data(as_text=True)
    order = _orders(env["db"])[0]
    assert order["total_cents"] == 0
    assert order["pay_status"] == "paid"
    redeemed = int(order["points_redeemed_cents"])
    assert redeemed > 0
    cx = sqlite3.connect(env["db"])
    try:
        assert P.balance(cx, EMAIL) == before - redeemed
    finally:
        cx.close()
    assert _ship_balance(env["db"]) == 5000 - int(order["ship_credit_applied_cents"])


def test_the_credit_is_spent_before_the_slow_steps_after_saving(env, monkeypatch):
    # Being paid releases the hold, so the debit must come straight after mark-paid,
    # before the portal welcome email and the other post-save steps (review of #1981).
    monkeypatch.setattr(app, "_ship_credit_enabled", lambda: True)
    _seed_ship_credit(env["db"], 20000)
    seen = []
    monkeypatch.setattr(app, "_hold_new_order_and_invite",
                        lambda cx, oid: seen.append(_ship_balance(env["db"])))
    assert env["client"].post("/api/cart/checkout", json={"address": ADDRESS}).status_code == 200
    applied = int(_orders(env["db"])[0]["ship_credit_applied_cents"])
    assert seen == [20000 - applied]
