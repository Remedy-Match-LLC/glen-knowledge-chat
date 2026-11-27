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
    app._t_db = db
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


def test_the_pdf_is_drawn_from_this_orders_summary(env, monkeypatch):
    """The renderer gets the same summary the email attachment uses, for this order."""
    app, c, oid = env
    from dashboard import invoice_pdf as P
    seen = []
    monkeypatch.setattr(P, "render_invoice_pdf", lambda o: seen.append(o) or b"%PDF-x")
    r = c.get(f"/api/console/invoice/{oid}/pdf", headers={"X-Console-Key": "k-test"})
    assert r.status_code == 200 and len(seen) == 1
    assert seen[0].get("external_ref") == "DS-331"
    assert seen[0].get("total_cents") == 4000


def test_a_superseded_order_prints_its_successor(env, monkeypatch):
    app, c, oid = env
    from dashboard import orders as O, invoice_pdf as P
    cx = sqlite3.connect(app._t_db); cx.row_factory = sqlite3.Row
    new = O.upsert_order(cx, source="dropship", external_ref="DS-332",
                         email="t@example.com", total_cents=5000, status="new")
    cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (new, oid))
    cx.commit(); cx.close()
    seen = []
    monkeypatch.setattr(P, "render_invoice_pdf", lambda o: seen.append(o) or b"%PDF-x")
    assert c.get(f"/api/console/invoice/{oid}/pdf",
                 headers={"X-Console-Key": "k-test"}).status_code == 200
    assert seen[0].get("external_ref") == "DS-332"


def test_refusals_are_not_cached(env):
    _app, c, _oid = env
    r = c.get("/api/console/invoice/999999/pdf", headers={"X-Console-Key": "k-test"})
    assert r.status_code == 404 and r.headers["Cache-Control"] == "no-store"
    assert c.get("/api/console/invoice/1/pdf").headers["Cache-Control"] == "no-store"
