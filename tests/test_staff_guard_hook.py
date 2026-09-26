"""The staff guard hook: a staff member inside a client's portal must confirm first.
Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md

Each guarded route's view function is swapped for a recorder, so "nothing ran" is
checked directly: no booking, no email, no Zoom and no Stripe call can happen when the
view itself never runs."""
import re
import sqlite3

import pytest

SECRET = "test-secret"
_VA = "va-token-guard"
_OWNER = "owner-token-guard"
PV = {"X-Portal-View": "1"}


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
    cx.close()
    return token


def _seed_user(appmod, name, scope, token):
    appmod._init_workspace_schema()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET scope=excluded.scope", (name, name, scope))
        uid = cx.execute("SELECT id FROM workspace_users WHERE name=?", (name,)).fetchone()[0]
        cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?,?)", (token, uid))
        cx.commit()


def _record(monkeypatch, appmod, path, method="POST"):
    """Replace the view behind `path` with one that records each call."""
    endpoint, _ = appmod.app.url_map.bind("localhost").match(path, method=method)
    calls = []

    def _view(**kw):
        calls.append(kw)
        return appmod.jsonify({"ran": True}), 200

    monkeypatch.setitem(appmod.app.view_functions, endpoint, _view)
    return calls


def _staff(extra=None, key=SECRET):
    h = {"X-Console-Key": key, **PV}
    h.update(extra or {})
    return h


def test_staff_booking_unconfirmed_is_refused_and_nothing_runs(client, monkeypatch):
    c, appmod = client
    calls = _record(monkeypatch, appmod, "/api/onboarding/book")
    r = c.post("/api/onboarding/book", json={}, headers=_staff())
    assert r.status_code == 409
    info = r.get_json()["staff_confirm"]
    assert "books a real appointment" in info["action"]
    assert calls == []


def test_staff_booking_confirmed_runs_once(client, monkeypatch):
    c, appmod = client
    calls = _record(monkeypatch, appmod, "/api/onboarding/book")
    r = c.post("/api/onboarding/book", json={}, headers=_staff({"X-Staff-Confirmed": "1"}))
    assert r.status_code == 200
    assert len(calls) == 1


def test_client_request_with_portal_header_is_never_asked(client, monkeypatch):
    c, appmod = client
    calls = _record(monkeypatch, appmod, "/api/onboarding/book")
    r = c.post("/api/onboarding/book", json={}, headers=PV)
    assert r.status_code == 200
    assert len(calls) == 1


def test_console_request_without_portal_header_is_untouched(client, monkeypatch):
    c, appmod = client
    calls = _record(monkeypatch, appmod, "/api/onboarding/book")
    r = c.post("/api/onboarding/book", json={}, headers={"X-Console-Key": SECRET})
    assert r.status_code == 200
    assert len(calls) == 1


def test_staff_fold_save_is_exempt(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/folds", method="PUT")
    r = c.put(f"/api/portal/{tok}/folds", json={"state": {}}, headers=_staff())
    assert r.status_code == 200
    assert len(calls) == 1


def test_staff_background_write_answers_204_without_running(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/process-request")
    r = c.post(f"/api/portal/{tok}/process-request", json={}, headers=_staff())
    assert r.status_code == 204
    assert calls == []


def test_va_token_counts_as_staff(client, monkeypatch):
    c, appmod = client
    _seed_user(appmod, "shaira", "workspace:shaira", _VA)
    tok = _seed_portal(appmod)
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=_staff(key=_VA))
    assert r.status_code == 409
    assert calls == []


def test_refusal_names_the_client(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=_staff())
    info = r.get_json()["staff_confirm"]
    assert info["client"] == "Brooke Webb"
    assert info["action"] == "This sends a chat message as Brooke Webb."


def test_garbage_key_is_not_staff(client, monkeypatch):
    c, appmod = client
    calls = _record(monkeypatch, appmod, "/api/onboarding/book")
    r = c.post("/api/onboarding/book", json={}, headers=_staff(key="not-a-real-key"))
    assert r.status_code == 200
    assert len(calls) == 1


def test_every_background_pattern_matches_a_real_route(client):
    """A pattern that matches no route silently turns a background write into a dialog."""
    _, appmod = client
    from dashboard import staff_guard as sg
    posts = [re.sub(r"<[^>]+>", "X", r.rule) for r in appmod.app.url_map.iter_rules()
             if {"POST", "PUT"} & set(r.methods or ())]
    for pat in sg.BACKGROUND + sg.EXEMPT:
        assert any(pat.search(p) for p in posts), f"{pat.pattern} matches no POST/PUT route"


def test_expired_staff_view_page_is_refused_not_run(client, monkeypatch):
    """A tab loaded as staff whose staff credential has since lapsed must not act as the
    client. The page marks itself; the server refuses without running."""
    c, appmod = client
    tok = _seed_portal(appmod)
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    h = {**PV, "X-Portal-Staff-View": "1"}
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=h)
    assert r.status_code == 409
    assert r.get_json() == {"staff_expired": True}
    assert calls == []
    bg = _record(monkeypatch, appmod, f"/api/portal/{tok}/process-request")
    assert c.post(f"/api/portal/{tok}/process-request", json={}, headers=h).status_code == 204
    assert bg == []
