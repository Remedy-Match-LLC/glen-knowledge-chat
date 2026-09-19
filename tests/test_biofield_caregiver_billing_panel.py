"""The Biofield invoice panel's "Bill with <caregiver>'s invoice" control.

Glen, 2026-09-19. The local app asks the console who may pay for this client and
what is remembered, and saves the tick. Raising the invoice is unchanged: the
console routes a remembered member's lines to the caregiver itself.
"""
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard.biofield_authoring import init_auth_tables, create_test


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _client(tmp_path, email, fake):
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Hershey Connour", email=email, date="2026-09-19")
    app = create_app(db_path=db, caregiver_billing=fake)
    app.testing = True
    return app.test_client(), tid


def test_get_reports_the_caregivers_and_the_remembered_one(tmp_path):
    seen = []

    def fake(email, caregiver_email=None, on=None):
        seen.append((email, caregiver_email, on))
        return {"ok": True, "caregivers": ["sharon@x.com"], "remembered": None}

    c, tid = _client(tmp_path, "hershey@x.com", fake)
    j = c.get(f"/author/{tid}/caregiver-billing").get_json()
    assert j == {"ok": True, "caregivers": ["sharon@x.com"], "remembered": None}
    assert seen == [("hershey@x.com", None, None)]


def test_post_saves_the_tick_for_this_client(tmp_path):
    seen = []

    def fake(email, caregiver_email=None, on=None):
        seen.append((email, caregiver_email, on))
        return {"ok": True, "caregivers": ["sharon@x.com"], "remembered": "sharon@x.com"}

    c, tid = _client(tmp_path, "hershey@x.com", fake)
    j = c.post(f"/author/{tid}/caregiver-billing",
               json={"caregiver_email": "sharon@x.com", "on": True}).get_json()
    assert j["remembered"] == "sharon@x.com"
    assert seen == [("hershey@x.com", "sharon@x.com", True)]


def test_a_console_failure_is_reported_not_hidden(tmp_path):
    c, tid = _client(tmp_path, "hershey@x.com", lambda *a, **k: {})
    r = c.get(f"/author/{tid}/caregiver-billing")
    assert r.status_code == 502 and r.get_json()["ok"] is False


def test_the_panel_carries_the_control(tmp_path):
    from dashboard import biofield_report_html as m
    import inspect
    js = inspect.getsource(m)
    assert "id=carerrow" in js and "loadCarer();" in js and "/caregiver-billing" in js
