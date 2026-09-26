"""Staff viewing a client's portal leave no trace as the client: GET routes skip their
writes, no client sign-in cookie is issued, and the portal data carries a banner field.
Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md"""
import sqlite3
from types import SimpleNamespace
from unittest import mock

import pytest

SECRET = "test-secret"
STAFF = {"X-Console-Key": SECRET}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed_portal(appmod, email="brooke@example.com", name="Brooke Webb"):
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, name, {"greeting": "Aloha."})
    from dashboard import portal_identity as pi
    pi._ensure_people_table(cx)       # the session bridge resolves the token to a person
    cx.close()
    return token


def _engaged(appmod, email="brooke@example.com"):
    from dashboard import notify_state as ns
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return ns.get_state(cx, email)["engaged"]


def test_staff_portal_load_is_not_engagement_and_carries_banner(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    r = c.get(f"/api/portal/{tok}", headers=STAFF)
    assert r.status_code == 200
    assert _engaged(appmod) is False
    assert r.get_json()["staff_view"] == {"client": "Brooke Webb"}


def test_client_portal_load_marks_engaged_and_has_no_banner(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    r = c.get(f"/api/portal/{tok}")
    assert r.status_code == 200
    assert _engaged(appmod) is True
    assert "staff_view" not in r.get_json()


def test_staff_page_open_sets_no_client_session_cookie(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    tok = _seed_portal(appmod)
    r = c.get(f"/portal/{tok}", headers=STAFF)
    assert r.status_code == 200
    assert "rm_portal_session" not in (r.headers.get("Set-Cookie") or "")


def test_client_page_open_still_sets_session_cookie(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    tok = _seed_portal(appmod)
    r = c.get(f"/portal/{tok}")
    assert r.status_code == 200
    assert "rm_portal_session" in (r.headers.get("Set-Cookie") or "")


def _coach_ctx(monkeypatch, appmod):
    monkeypatch.setattr(appmod, "_member_thread_ctx",
                        lambda cx, token: ("m@x.com", {"request_id": None, "coach_email": "c@x.com"}))


def _threads(appmod):
    from dashboard import coach_threads as ct
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ct.init_thread_tables(cx)
        return cx.execute("SELECT COUNT(*) FROM coach_threads").fetchone()[0]


def test_staff_coach_thread_read_creates_nothing_and_marks_nothing(client, monkeypatch):
    c, appmod = client
    _coach_ctx(monkeypatch, appmod)
    from dashboard import coach_threads as ct
    with mock.patch.object(ct, "mark_read") as mr:
        r = c.get("/api/coach-thread/member?token=t", headers=STAFF)
    assert r.status_code == 200
    assert r.get_json()["messages"] == []
    assert mr.called is False
    assert _threads(appmod) == 0


def test_client_coach_thread_read_marks_read(client, monkeypatch):
    c, appmod = client
    _coach_ctx(monkeypatch, appmod)
    from dashboard import coach_threads as ct
    with mock.patch.object(ct, "mark_read") as mr:
        r = c.get("/api/coach-thread/member?token=t")
    assert r.status_code == 200
    assert mr.called is True
    assert _threads(appmod) == 1


def _peer_ctx(monkeypatch, appmod):
    monkeypatch.setattr(appmod, "_evox_ident",
                        lambda cx, token: SimpleNamespace(email="m@x.com", name="Mel"))
    thread = {"coach_email": "c@x.com", "member_email": "m@x.com", "status": "active",
              "active_epoch": 1}
    monkeypatch.setattr(appmod, "_peer_thread_role", lambda cx, tid, email: (thread, "member"))
    monkeypatch.setattr(appmod, "_peer_first_name", lambda cx, e: "Kai")


@pytest.mark.parametrize("headers,want_called", [(STAFF, False), ({}, True)])
def test_peer_thread_read_marks_read_only_for_the_client(client, monkeypatch, headers, want_called):
    c, appmod = client
    _peer_ctx(monkeypatch, appmod)
    from dashboard import coach_threads as ct
    with mock.patch.object(ct, "mark_read") as mr, \
            mock.patch.object(ct, "messages", return_value=[]):
        r = c.get("/api/peer-thread/7?token=t", headers=headers)
    assert r.status_code == 200
    assert mr.called is want_called


@pytest.mark.parametrize("headers,want_called", [(STAFF, False), ({}, True)])
def test_peer_state_self_heal_only_for_the_client(client, monkeypatch, headers, want_called):
    c, appmod = client
    monkeypatch.setattr(appmod, "_peer_ident_paid", lambda cx, token: ("m@x.com", False))
    from dashboard import peer_connect as pc
    monkeypatch.setattr(pc, "is_opted_in", lambda cx, email: True)
    with mock.patch.object(pc, "set_optin") as so:
        r = c.get("/api/peer/state?token=t", headers=headers)
    assert r.status_code == 200
    assert so.called is want_called
