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
    r = c.get(f"/api/console/portal-link?email={email}&staff_open=1",
              headers={"X-Console-Key": SECRET})
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


def test_link_for_sending_to_the_client_carries_no_pass(client):
    """The console's copy box sends this link to the client. A pass in it would put the
    client into staff view (review rounds 1 and 2, 2026-09-26)."""
    c, appmod = client
    _seed_portal(appmod)
    r = c.get("/api/console/portal-link?email=brooke@example.com",
              headers={"X-Console-Key": SECRET})
    assert "sp=" not in r.get_json()["link"]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        n = cx.execute("SELECT COUNT(*) FROM auth_tokens WHERE purpose='staff_view_pass'").fetchone()[0]
    assert n == 0


def _refused_page(r):
    assert r.status_code == 200
    assert "Open the portal again from the console" in r.get_data(as_text=True)
    assert "rm_staff_view=" not in _cookies(r)
    assert "rm_portal_session" not in _cookies(r)


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
    _refused_page(c2.get(f"/portal/{tok}?sp={sp}"))


def test_expired_pass_sets_nothing(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    sp = _pass_for(c)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE auth_tokens SET expires_at='2000-01-01T00:00:00+00:00' "
                   "WHERE purpose='staff_view_pass'")
    _refused_page(c.get(f"/portal/{tok}?sp={sp}"))


def test_pass_for_another_portal_sets_nothing(client):
    c, appmod = client
    _seed_portal(appmod)
    other = _seed_portal(appmod, email="kai@example.com", name="Kai")
    sp = _pass_for(c)                         # minted for brooke
    _refused_page(c.get(f"/portal/{other}?sp={sp}"))


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


# ── Review round 3, 2026-09-26 ──────────────────────────────────────────────────────

def test_a_token_of_another_purpose_is_not_a_pass(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO auth_tokens (token_hash, email, purpose, created_at, expires_at) "
                   "VALUES (?,?,?,?,?)", (appmod._hash_token("magic-abc"), "brooke@example.com",
                                          "magic_link", "2026-01-01T00:00:00+00:00",
                                          "2999-01-01T00:00:00+00:00"))
        cx.commit()
    _refused_page(c.get(f"/portal/{tok}?sp=magic-abc"))


def test_pass_lasts_ten_minutes_and_cookie_is_httponly(client):
    from datetime import datetime
    c, appmod = client
    tok = _seed_portal(appmod)
    sp = _pass_for(c)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        made, exp = cx.execute("SELECT created_at, expires_at FROM auth_tokens "
                               "WHERE purpose='staff_view_pass'").fetchone()
    assert (datetime.fromisoformat(exp) - datetime.fromisoformat(made)).total_seconds() == 600
    ck = _cookies(c.get(f"/portal/{tok}?sp={sp}"))
    assert "HttpOnly" in ck


def _signed(appmod, exp):
    return f"{exp}.{appmod._staff_view_sig(exp)}"


def test_lapsed_staff_cookie_fails_closed(client, monkeypatch):
    """A staff browser whose 12-hour view lapsed must not turn back into the client:
    no client sign-in, no engagement, and writes are refused as expired."""
    c, appmod = client
    monkeypatch.setattr(appmod, "_WISHLIST_ENABLED", True)
    merged = []
    monkeypatch.setattr(appmod, "_wishlist_merge_with_self", lambda *a, **k: merged.append(a))
    tok = _seed_portal(appmod)
    c.set_cookie("rm_staff_view", _signed(appmod, 1000))          # signed, long expired
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    r = c.post(f"/api/portal/{tok}/chat", json={}, headers=PV)
    assert r.status_code == 409 and r.get_json() == {"staff_expired": True}
    assert calls == []
    d = c.get(f"/api/portal/{tok}").get_json()
    assert d["staff_view"] == {"client": "Brooke Webb", "expired": True}
    assert merged == []
    from dashboard import notify_state as ns
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ns.get_state(cx, "brooke@example.com")["engaged"] is False
    assert "rm_portal_session" not in _cookies(c.get(f"/portal/{tok}"))


def test_no_console_secret_means_no_staff_cookie(client, monkeypatch):
    c, appmod = client
    import dashboard
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "")
    tok = _seed_portal(appmod)
    c.set_cookie("rm_staff_view", _signed(appmod, 9999999999))
    calls = _record(monkeypatch, appmod, f"/api/portal/{tok}/chat")
    assert c.post(f"/api/portal/{tok}/chat", json={}, headers=PV).status_code == 200
    assert len(calls) == 1


def test_staff_cookie_folds_never_write_the_client_record(client, monkeypatch):
    c, appmod = client
    monkeypatch.setenv("PORTAL_FOLDS_V2", "1")
    tok = _seed_portal(appmod)
    c.get(f"/portal/{tok}?sp={_pass_for(c)}")
    r = c.put(f"/api/portal/{tok}/folds", json={"state": {"cards": {"x": True}}}, headers=PV)
    assert r.status_code == 200
    assert c.get(f"/api/portal/{tok}/folds").get_json()["viewer"] == "staff"
    fresh = appmod.app.test_client()                               # the client's own browser
    assert fresh.get(f"/api/portal/{tok}/folds").get_json()["state"].get("cards", {}) == {}


# ── The local Biofield app's "View Client Portal" (Glen, 2026-09-26: "fix those two paths")

def _local_app_link(c, staff_open):
    body = {"email": "brooke@example.com", "name": "Brooke Webb"}
    if staff_open:
        body["staff_open"] = True
    r = c.post("/admin/portal/get-or-create-link", json=body, headers={"X-Console-Key": SECRET})
    assert r.status_code == 200
    return r.get_json()["url"]


def test_local_app_open_link_carries_a_working_pass(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    url = _local_app_link(c, staff_open=True)
    sp = parse_qs(urlparse(url).query)["sp"][0]
    r = c.get(f"/portal/{tok}?sp={sp}")
    assert r.status_code == 302 and "rm_staff_view=" in _cookies(r)


def test_rollout_link_for_clients_carries_no_pass(client):
    """The same route makes the links the rollout emails to clients."""
    c, appmod = client
    _seed_portal(appmod)
    assert "sp=" not in _local_app_link(c, staff_open=False)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        n = cx.execute("SELECT COUNT(*) FROM auth_tokens WHERE purpose='staff_view_pass'").fetchone()[0]
    assert n == 0
