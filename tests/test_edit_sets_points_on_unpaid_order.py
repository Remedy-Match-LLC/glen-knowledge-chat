"""The invoice editor may set points on an UNPAID order, within the balance and floor.

Glen, 2026-09-19, on Sharon Connour's merged household order: "apply her points to 194."
The editor had no way to do it: it carries an order's existing points forward on purpose,
because points leave the ledger when the order is PAID (_settle_order_points, keyed on
external_ref). Before payment nothing has been spent, so setting the figure is safe.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import rbac as _rbac

EMAIL = "carer@x.com"


def _app(tmp_path, monkeypatch, *, pay_status="unpaid", balance=2713, redeemed_ref=False):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O, points as P
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        O.upsert_order(cx, source="in-house", external_ref="INH-P1", status="proposed",
                       email=EMAIL, name="Carer", items=[{"slug": "ff", "qty": 1}],
                       total_cents=10000)
        if pay_status == "paid":
            cx.execute("UPDATE orders SET pay_status='paid' WHERE id=1")
        P.init_points_table(cx)
        if balance:
            P.credit(cx, EMAIL, value_cents=balance, reason="earn", order_ref="OLD-1")
        if redeemed_ref:
            P.spend(cx, EMAIL, value_cents=100, reason="redeem", order_ref="INH-P1")
        cx.commit()
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_bos_actor", lambda: _rbac.Actor(role="owner", name="glen"))
    # One $100 item listed at $100: the 43% points floor leaves $57 of room.
    monkeypatch.setattr(appmod, "_get_product",
                        lambda slug: {"slug": "ff", "name": "FF", "price_cents": 10000})
    monkeypatch.setattr(appmod, "_price_inhouse_invoice", lambda *a, **k: {
        "items_rec": [{"slug": "ff", "name": "FF", "qty": 1, "unit_cents": 10000,
                       "line_cents": 10000}],
        "cart": [{"slug": "ff", "qty": 1}], "subtotal_cents": 10000, "shipping_cents": 0,
        "get_cents": 0, "discount_cents": 0, "adjustment_cents": 0,
        "points_redeemed_cents": 0, "total_cents": 10000})
    monkeypatch.setattr(appmod, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    return appmod.app.test_client(), db


def _order(db):
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return O.get_order(cx, 1)


def _edit(client, **extra):
    return client.post("/api/orders/1/edit", json={"lines": [{"slug": "ff", "qty": 1}], **extra})


def test_points_are_set_on_an_unpaid_order_up_to_the_balance(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch)
    r = _edit(client, points_redeem_cents=2713)
    assert r.status_code == 200, r.get_json()
    o = _order(db)
    assert o["points_redeemed_cents"] == 2713
    assert o["total_cents"] == 10000 - 2713


def test_the_balance_caps_the_request(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch, balance=500)
    assert _edit(client, points_redeem_cents=2713).status_code == 200
    assert _order(db)["points_redeemed_cents"] == 500


def test_the_points_floor_caps_the_request(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch, balance=90000)
    assert _edit(client, points_redeem_cents=90000).status_code == 200
    assert _order(db)["points_redeemed_cents"] == 10000 - 4300   # floor is 43% of list


def test_a_paid_order_is_refused_and_untouched(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch, pay_status="paid")
    r = _edit(client, points_redeem_cents=2713)
    assert r.status_code == 400 and "unpaid" in r.get_json()["error"]
    assert int(_order(db)["points_redeemed_cents"] or 0) == 0


def test_an_order_whose_points_already_left_the_ledger_is_refused(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch, redeemed_ref=True)
    r = _edit(client, points_redeem_cents=2713)
    assert r.status_code == 400 and "already redeemed" in r.get_json()["error"]


def test_without_the_field_the_existing_points_carry_forward(tmp_path, monkeypatch):
    client, db = _app(tmp_path, monkeypatch)
    _edit(client, points_redeem_cents=1000)
    assert _edit(client).status_code == 200
    assert _order(db)["points_redeemed_cents"] == 1000
