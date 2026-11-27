"""youcloned-6d, 27 Nov: a script needs the clean invoice PDF. Console key, header only."""
import importlib
import sqlite3

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = importlib.import_module("app")
    db = str(tmp_path / "o.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "k-test")
    from dashboard import orders as O
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    oid = O.upsert_order(cx, source="dropship", external_ref="DS-331",
                         email="t@example.com", total_cents=4000, status="new")
    cx.commit()
    cx.close()
    return app, app.app.test_client(), oid


def test_a_script_gets_the_pdf(env):
    app, c, oid = env
    r = c.get(f"/api/console/invoice/{oid}/pdf", headers={"X-Console-Key": "k-test"})
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.data[:5] == b"%PDF-"
    assert "filename=" in r.headers["Content-Disposition"]
    assert r.headers["Cache-Control"] == "no-store"


def test_no_key_no_pdf(env):
    _app, c, oid = env
    assert c.get(f"/api/console/invoice/{oid}/pdf").status_code == 401
    assert c.get(f"/api/console/invoice/{oid}/pdf?key=k-test").status_code == 401
    assert c.get(f"/api/console/invoice/{oid}/pdf",
                 headers={"X-Console-Key": "wrong"}).status_code == 401


def test_an_unknown_order_is_404(env):
    _app, c, _oid = env
    assert c.get("/api/console/invoice/999999/pdf",
                 headers={"X-Console-Key": "k-test"}).status_code == 404
