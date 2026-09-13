"""Owner routes that hold or release review emails for one order.

These need the full app import, so only CI can judge them.
"""
import importlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


def _ago(days):
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


@pytest.fixture
def env(monkeypatch, tmp_path):
    appmod = _app()
    db = str(tmp_path / "h.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: type("A", (), {"role": appmod._bos_rbac.OWNER})())
    from dashboard import orders as O
    from dashboard import review_invites as RI
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        RI.init_table(cx)
        cur = cx.execute(
            "INSERT INTO orders (created_at, source, external_ref, email, name, "
            "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (_ago(22), "test", "o1", "b@x.com", "Buyer",
             json.dumps([{"slug": "wholomega", "name": "WholOmega", "qty": 1}]),
             "shipped", _ago(20)))
        oid = cur.lastrowid
        cx.commit()
    appmod.app.config["TESTING"] = True
    return appmod, db, RI, oid


def test_hold_then_release_through_the_routes(env):
    appmod, db, RI, oid = env
    c = appmod.app.test_client()

    r = c.post(f"/api/orders/{oid}/review-hold",
               json={"slug": "wholomega", "reason": "never arrived"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["holds"][0]["slug"] == "wholomega"
    with sqlite3.connect(db) as cx:
        assert RI.pending(cx, days=14) == []

    got = c.get(f"/api/orders/{oid}/review-hold").get_json()
    assert [h["reason"] for h in got["holds"]] == ["never arrived"]

    r = c.post(f"/api/orders/{oid}/review-hold/release", json={"slug": "wholomega"})
    assert r.status_code == 200
    assert r.get_json()["holds"] == []
    with sqlite3.connect(db) as cx:
        assert [p["slug"] for p in RI.pending(cx, days=14)] == ["wholomega"]


def test_hold_requires_the_owner(env, monkeypatch):
    appmod, db, RI, oid = env
    monkeypatch.setattr(appmod, "_bos_actor", lambda: None)
    c = appmod.app.test_client()
    assert c.post(f"/api/orders/{oid}/review-hold", json={}).status_code == 401
    assert c.get(f"/api/orders/{oid}/review-hold").status_code == 401
    with sqlite3.connect(db) as cx:
        assert RI.holds_for(cx, oid) == []


def test_hold_on_an_unknown_order_is_404(env):
    appmod, db, RI, oid = env
    c = appmod.app.test_client()
    assert c.post(f"/api/orders/{oid + 999}/review-hold", json={}).status_code == 404
