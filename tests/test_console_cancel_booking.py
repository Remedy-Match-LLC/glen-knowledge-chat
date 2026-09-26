"""Owner-only booking cancel that sends nothing.

Glen, 2026-09-26: a welcome call booked by mistake from a console-opened portal is to be
cancelled 'with a short apology' that he sends himself. So this route frees the slot,
takes the event off the Live Calendar, stops reminders, and sends no email.
"""
import sqlite3

import pytest

SECRET = "test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    appmod.app.config["TESTING"] = True
    sent = []
    monkeypatch.setattr(appmod, "send_email", lambda *a, **k: sent.append((a, k)), raising=False)
    monkeypatch.setattr(appmod, "_onboarding_send_confirmations", lambda *a, **k: sent.append(a),
                        raising=False)
    return appmod.app.test_client(), appmod, sent


def _book(appmod, email="pam@x.com", start="2026-09-28T14:30:00"):
    from dashboard import evox as ev
    appmod._init_calendar_table()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        ev.init_evox_tables(cx)
        return ev.create_booking(cx, email, start, duration_min=15, practitioner="rae",
                                 session_type="onboarding", medium="phone")


def _rows(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    try:
        b = cx.execute("SELECT id, status FROM evox_bookings").fetchall()
        e = cx.execute("SELECT google_event_id, status FROM calendar_events").fetchall()
        return b, e
    finally:
        cx.close()


URL = "/api/console/bookings/cancel"


def test_owner_only(client):
    c, appmod, _ = client
    b = _book(appmod)
    assert c.post(URL, json={"id": b["id"], "apply": True}).status_code == 401


def test_dry_run_changes_nothing(client):
    c, appmod, sent = client
    b = _book(appmod)
    before = _rows(appmod)
    r = c.post(URL, json={"id": b["id"]}, headers={"X-Console-Key": SECRET}).get_json()
    assert r["applied"] is False and r["booking"]["status"] == "booked"
    assert _rows(appmod) == before and sent == []


def test_apply_cancels_hides_and_sends_nothing(client):
    c, appmod, sent = client
    b = _book(appmod)
    r = c.post(URL, json={"id": b["id"], "apply": True}, headers={"X-Console-Key": SECRET}).get_json()
    assert r["applied"] is True
    bookings, events = _rows(appmod)
    assert bookings == [(b["id"], "cancelled")]
    assert events == [(f"onboarding-{b['id']}", "cancelled")]
    assert sent == []


def test_unknown_or_already_cancelled(client):
    c, appmod, _ = client
    assert c.post(URL, json={"id": 999, "apply": True}, headers={"X-Console-Key": SECRET}).status_code == 404
    b = _book(appmod)
    c.post(URL, json={"id": b["id"], "apply": True}, headers={"X-Console-Key": SECRET})
    r = c.post(URL, json={"id": b["id"], "apply": True}, headers={"X-Console-Key": SECRET})
    assert r.status_code == 409


def test_find_by_email_and_start(client):
    c, appmod, _ = client
    b = _book(appmod)
    r = c.post(URL, json={"email": "PAM@x.com", "start": "2026-09-28T14:30:00"},
               headers={"X-Console-Key": SECRET}).get_json()
    assert r["booking"]["id"] == b["id"]
