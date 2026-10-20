"""A Plan report links to /portal/me#chat. A client who is not signed in must land on
#chat after signing in, even when the emailed link opens in another browser.

The hash never reaches the server, so the portal page sends it to /portal/login as
?next=<key>, the login page posts it, and the server carries it on the emailed link.
Only a key from dashboard/portal_return.ROUTE_KEYS survives: an unknown key or a URL
is dropped, so `next` can never redirect off the portal.
"""
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from dashboard import portal_return as ret

ROOT = Path(__file__).resolve().parent.parent
PORTAL = (ROOT / "static" / "client-portal.html").read_text()
LOGIN = (ROOT / "static" / "client-login.html").read_text()

HOSTILE = ["https://evil.example", "//evil.example", "/\\evil.example", "nope",
           "chat/../../x", "chat#x", "chat?x=1", "javascript:alert(1)", "",
           None, "a" * 50, "constructor"]


# ── The whitelist ───────────────────────────────────────────────────────────

def _js_route_keys():
    m = re.search(r"const PORTAL_HASH_ROUTES = \{(.*?)\n\};", PORTAL, re.S)
    assert m, "PORTAL_HASH_ROUTES not found"
    keys = set()
    for line in m.group(1).splitlines():
        km = re.match(r'\s*"?([a-z-]+)"?\s*:\s*\{', line)
        if km:
            keys.add(km.group(1))
    return keys


def test_the_server_list_matches_the_portal_routes():
    keys = _js_route_keys()
    assert "chat" in keys and "intake" in keys
    assert set(ret.ROUTE_KEYS) == keys, (
        "dashboard/portal_return.ROUTE_KEYS and PORTAL_HASH_ROUTES disagree: "
        f"server only {sorted(set(ret.ROUTE_KEYS) - keys)}, page only {sorted(keys - set(ret.ROUTE_KEYS))}")


@pytest.mark.parametrize("value,want", [("chat", "chat"), ("#chat", "chat"), ("Chat", "chat"),
                                        ("intake", "intake"), ("calendar", "calendar")])
def test_a_known_key_is_kept(value, want):
    assert ret.clean_next(value) == want
    assert ret.portal_me(value) == "/portal/me#" + want
    assert ret.query(value) == "&next=" + want


@pytest.mark.parametrize("value", HOSTILE)
def test_anything_else_is_dropped(value):
    assert ret.clean_next(value) == ""
    assert ret.portal_me(value) == "/portal/me"
    assert ret.query(value) == ""


# ── The routes ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    monkeypatch.setattr(appmod, "_equalise_response_time", lambda *a, **k: None)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed_person(appmod, email, name="C"):
    from dashboard import portal_identity as pi
    cx = sqlite3.connect(appmod.LOG_DB)
    pi._ensure_people_table(cx)
    cx.execute("INSERT OR IGNORE INTO people (email, name, roles, created_at, updated_at) "
               "VALUES (?,?,?,?,?)", (email, name, '["client"]', "t", "t"))
    cx.commit()
    cx.close()


def _request_link(c, appmod, monkeypatch, email, nxt):
    sent = {}
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.update(body=body) or ("t", None))
    _seed_person(appmod, email)
    body = {"email": email}
    if nxt is not None:
        body["next"] = nxt
    r = c.post("/portal/login-request", json=body)
    assert r.status_code == 200
    return re.search(r"(\S*/portal/login-verify\?\S+)", sent["body"]).group(1)


def _sign_in(c, link):
    q = parse_qs(urlparse(link).query)
    page = c.get(urlparse(link).path + "?" + urlparse(link).query)
    assert page.status_code == 200
    form = {"token": q["token"][0]}
    hidden = re.search(r"name='next' value='([^']*)'", page.get_data(as_text=True))
    if hidden:
        form["next"] = hidden.group(1)
    return page, c.post("/portal/login-verify", data=form)


def test_chat_survives_the_magic_link(client, monkeypatch):
    c, appmod = client
    link = _request_link(c, appmod, monkeypatch, "next-chat@example.com", "chat")
    assert link.endswith("&next=chat")
    page, done = _sign_in(c, link)
    assert "name='next' value='chat'" in page.get_data(as_text=True)
    assert done.status_code in (302, 303)
    assert done.headers["Location"].endswith("/portal/me#chat")
    assert "rm_portal_session=" in done.headers.get("Set-Cookie", "")


def test_a_hash_sent_as_is_is_kept(client, monkeypatch):
    c, appmod = client
    link = _request_link(c, appmod, monkeypatch, "next-hash@example.com", "#intake")
    _page, done = _sign_in(c, link)
    assert done.headers["Location"].endswith("/portal/me#intake")


@pytest.mark.parametrize("nxt", [None, "nope", "https://evil.example", "//evil.example",
                                 "javascript:alert(1)"])
def test_no_or_bad_next_signs_in_to_the_plain_portal(client, monkeypatch, nxt):
    c, appmod = client
    link = _request_link(c, appmod, monkeypatch, "next-bad@example.com", nxt)
    assert "next=" not in link and "evil" not in link
    _page, done = _sign_in(c, link)
    assert done.headers["Location"].endswith("/portal/me")


@pytest.mark.parametrize("nxt", ["https://evil.example", "//evil.example", "nope",
                                 "chat%0d%0aSet-Cookie:x=1"])
def test_a_tampered_link_never_redirects_off_the_portal(client, monkeypatch, nxt):
    """Someone edits next= on the emailed link itself: the verify route checks again."""
    c, appmod = client
    link = _request_link(c, appmod, monkeypatch, "next-tamper@example.com", None)
    token = parse_qs(urlparse(link).query)["token"][0]
    page = c.get(f"/portal/login-verify?token={token}&next={nxt}")
    assert "name='next'" not in page.get_data(as_text=True)
    done = c.post("/portal/login-verify", data={"token": token, "next": nxt})
    loc = done.headers["Location"]
    assert loc.endswith("/portal/me") and "evil" not in loc


def test_a_signed_in_back_button_keeps_the_route(client, monkeypatch):
    """A dead token with a live session goes to the portal, still on #chat."""
    c, appmod = client
    link = _request_link(c, appmod, monkeypatch, "next-back@example.com", "chat")
    _page, done = _sign_in(c, link)
    token = parse_qs(urlparse(link).query)["token"][0]
    again = c.get(f"/portal/login-verify?token={token}&next=chat")
    assert again.headers["Location"].endswith("/portal/me#chat")
    again = c.post("/portal/login-verify", data={"token": token, "next": "evil"})
    assert again.headers["Location"].endswith("/portal/me")


def _google(c, appmod, monkeypatch, subject, email, start_q=""):
    monkeypatch.setattr(appmod, "_portal_google_login_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("PORTAL_GOOGLE_CLIENT_SECRET", "google-secret")
    from dashboard import portal_providers as providers
    monkeypatch.setattr(providers, "exchange_google", lambda *a, **k: {
        "provider": "google", "subject": subject, "email": email, "name": ""})
    start = c.get("/portal/auth/google/start" + start_q)
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    return start, c.get(f"/portal/auth/google/callback?state={state}&code=code")


@pytest.mark.parametrize("start_q,want", [("?next=chat", "/portal/me#chat"),
                                          ("?next=https://evil.example", "/portal/me"),
                                          ("", "/portal/me")])
def test_google_sign_in_keeps_the_route(client, monkeypatch, start_q, want):
    c, appmod = client
    email = "g-next@example.com"
    _seed_person(appmod, email)
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(appmod.LOG_DB)
    pid = cx.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()[0]
    pa.link_external_identity(cx, pid, "google", "g-next-sub", email)
    cx.close()
    _start, callback = _google(c, appmod, monkeypatch, "g-next-sub", email, start_q)
    assert callback.status_code in (302, 303)
    assert callback.headers["Location"] == want


def test_google_first_link_carries_the_route_through_the_confirm_email(client, monkeypatch):
    c, appmod = client
    email = "g-first@example.com"
    _seed_person(appmod, email)
    sent = {}
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.update(body=body) or ("t", None))
    _start, callback = _google(c, appmod, monkeypatch, "g-first-sub", email, "?next=chat")
    assert callback.status_code == 200
    link = re.search(r"(\S*/portal/auth/link-confirm\?\S+)", sent["body"]).group(1)
    assert link.endswith("&next=chat")
    q = parse_qs(urlparse(link).query)
    page = c.get("/portal/auth/link-confirm?" + urlparse(link).query)
    assert "name='next' value='chat'" in page.get_data(as_text=True)
    done = c.post("/portal/auth/link-confirm", data={"token": q["token"][0], "next": "chat"})
    assert done.headers["Location"] == "/portal/me#chat"


def test_password_sign_in_returns_the_route(client, monkeypatch):
    pytest.importorskip("argon2")
    c, appmod = client
    monkeypatch.setattr(appmod, "_portal_password_login_enabled", lambda: True)
    email = "pw-next@example.com"
    _seed_person(appmod, email)
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(appmod.LOG_DB)
    pid = cx.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()[0]
    pa.set_password(cx, pid, "correct horse battery staple 9")
    cx.commit()
    cx.close()
    ok = c.post("/portal/password-login", json={"email": email, "next": "chat",
                                                 "password": "correct horse battery staple 9"})
    assert ok.get_json()["redirect"] == "/portal/me#chat"
    bad = c.post("/portal/password-login", json={"email": email, "next": "//evil.example",
                                                  "password": "correct horse battery staple 9"})
    assert bad.get_json()["redirect"] == "/portal/me"


# ── The two pages ───────────────────────────────────────────────────────────

def test_the_login_page_sends_next_with_every_sign_in():
    assert "body:JSON.stringify({email, next:portalNext})" in LOGIN
    assert "body:JSON.stringify({email,password:secret,next:portalNext})" in LOGIN
    assert '"?next=" + encodeURIComponent(portalNext)' in LOGIN
    assert "/^[a-z-]{1,40}$/.test(v)" in LOGIN
