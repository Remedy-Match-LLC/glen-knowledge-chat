"""The staff pass: how the guard sees staff on the portal's own domain.

The console runs on one domain and portals on another, so the console login cookie never
reaches the portal. The console's portal link carries a one-time pass that the portal
swaps for a restrict-only staff cookie.
Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md
"""
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

SECRET = "test-secret"
PV = {"X-Portal-View": "1"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed_portal(appmod, email="brooke@example.com", name="Brooke Webb"):
    from dashboard import client_portal as cp
    from dashboard import portal_identity as pi
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, name, {"greeting": "Aloha."})
    pi._ensure_people_table(cx)
    cx.close()
    return token


def _pass_for(c, email="brooke@example.com"):
    r = c.get(f"/api/console/portal-link?email={email}", headers={"X-Console-Key": SECRET})
    assert r.status_code == 200, r.get_data(as_text=True)
    link = r.get_json()["link"]
    return parse_qs(urlparse(link).query)["sp"][0]


def _cookies(resp):
    return " ".join(resp.headers.getlist("Set-Cookie"))


def _record(monkeypatch, appmod, path):
    endpoint, _ = appmod.app.url_map.bind("localhost").match(path, method="POST")
    calls = []

    def _view(**kw):
        calls.append(kw)
        return appmod.jsonify({"ran": True}), 200

    monkeypatch.setitem(appmod.app.view_functions, endpoint, _view)
    return calls


def test_console_link_carries_a_pass(client):
    c, appmod = client
    _seed_portal(appmod)
    assert len(_pass_for(c)) >= 20


def test_pass_sets_staff_cookie_strips_itself_and_gives_no_client_session(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    sp = _pass_for(c)
    r = c.get(f"/portal/{tok}?sp={sp}&from=console")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "sp=" not in loc and f"/portal/{tok}" in loc and "from=console" in loc
    assert "rm_staff_view=" in _cookies(r)
    assert "rm_portal_session" not in _cookies(r)


def test_pass_works_once(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    sp = _pass_for(c)
    c.get(f"/portal/{tok}?sp={sp}")
    c2 = appmod.app.test_client()
    r = c2.get(f"/portal/{tok}?sp={sp}")
    assert "rm_staff_view=" not in _cookies(r)


def test_expired_pass_sets_nothing(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    sp = _pass_for(c)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE auth_tokens SET expires_at='2000-01-01T00:00:00+00:00' "
                   "WHERE purpose='staff_view_pass'")
    r = c.get(f"/portal/{tok}?sp={sp}")
    assert "rm_staff_view=" not in _cookies(r)


def test_pass_for_another_portal_sets_nothing(client):
    c, appmod = client
    _seed_portal(appmod)
    other = _seed_portal(appmod, email="kai@example.com", name="Kai")
    sp = _pass_for(c)                         # minted for brooke
    r = c.get(f"/portal/{other}?sp={sp}")
    assert "rm_staff_view=" not in _cookies(r)


def test_staff_cookie_alone_makes_portal_writes_ask_and_shows_banner(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    c.get(f"/portal/{tok}?sp={_pass_for(c)}")
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=PV)     # no console key at all
    assert r.status_code == 409
    assert calls == []
    assert c.get(f"/api/portal/{tok}").get_json()["staff_view"] == {"client": "Brooke Webb"}


def test_forged_staff_cookie_is_not_staff(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    c.set_cookie("rm_staff_view", "9999999999.deadbeef")
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=PV)
    assert r.status_code == 200
    assert len(calls) == 1


def test_staff_cookie_opens_no_console_route(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    c.get(f"/portal/{tok}?sp={_pass_for(c)}")
    r = c.get("/api/console/portal-link?email=brooke@example.com")
    assert r.status_code == 401
