"""An open invoice keeps the price it was issued at, through an edit.

Glen, 2026-10-01, decision 3 of the whole-dollar plan: "Open invoices and quotes keep the
price they were issued at." Before this, editing an unpaid invoice re-priced every line
that had no typed price, so a $69.97 line issued before whole dollars came back at $70,
and a pre-rounding member line at $49.68 came back at $50. A rise of a dollar or more
is a real change (a membership removed, a quantity rule) and still applies.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import rbac as _rbac

EMAIL = "buyer@x.com"
PRODUCT = {"slug": "thing", "name": "Thing", "price_cents": 7000}


def _app(tmp_path, monkeypatch, *, unit_cents=6997, qty=1, pay_status="unpaid"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = str(tmp_path / "chat_log.db")
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        O.upsert_order(cx, source="in-house", external_ref="INH-W1", status="proposed",
                       email=EMAIL, name="Buyer", channel="pickup",
                       items=[{"slug": "thing", "name": "Thing", "qty": qty,
                               "unit_cents": unit_cents, "line_cents": unit_cents * qty}],
                       total_cents=unit_cents * qty)
        if pay_status == "paid":
            cx.execute("UPDATE orders SET pay_status='paid' WHERE id=1")
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
    monkeypatch.setattr(appmod, "_get_product",
                        lambda slug: dict(PRODUCT) if slug == "thing" else None)
    monkeypatch.setattr(appmod, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    return appmod, appmod.app.test_client(), db


def _order(db):
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return O.get_order(cx, 1)


def _edit(client, lines):
    r = client.post("/api/orders/1/edit", json={"lines": lines, "pickup": True})
    assert r.status_code == 200, r.get_json()
    return r


def test_an_unchanged_line_keeps_its_issued_price(tmp_path, monkeypatch):
    _, client, db = _app(tmp_path, monkeypatch, unit_cents=6997)
    _edit(client, [{"slug": "thing", "qty": 1}])
    o = _order(db)
    assert o["items"][0]["unit_cents"] == 6997
    assert o["total_cents"] == 6997


def test_a_changed_quantity_is_priced_fresh(tmp_path, monkeypatch):
    _, client, db = _app(tmp_path, monkeypatch, unit_cents=6997)
    _edit(client, [{"slug": "thing", "qty": 2}])
    assert _order(db)["items"][0]["unit_cents"] == 7000


def test_a_rise_of_a_dollar_or_more_still_applies(tmp_path, monkeypatch):
    """A member line at $50 whose membership is gone goes back to list."""
    _, client, db = _app(tmp_path, monkeypatch, unit_cents=5000)
    _edit(client, [{"slug": "thing", "qty": 1}])
    assert _order(db)["items"][0]["unit_cents"] == 7000


def test_a_typed_price_is_the_owners_and_wins(tmp_path, monkeypatch):
    _, client, db = _app(tmp_path, monkeypatch, unit_cents=6997)
    _edit(client, [{"slug": "thing", "qty": 1, "unit_cents": 6500}])
    assert _order(db)["items"][0]["unit_cents"] == 6500


def test_the_rule_itself():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    keep = appmod._keep_issued_unit
    assert keep(None, 7000) == 7000          # no issued price: re-price
    assert keep(6997, 7000) == 6997          # a sub-dollar rise: keep what was issued
    assert keep(4968, 5000) == 4968
    assert keep(6900, 7000) == 7000          # a full dollar is a real change
    assert keep(7000, 6000) == 6000          # a fall always applies
