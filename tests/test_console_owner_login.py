import re

import pytest


@pytest.fixture
def owner_client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "emergency-secret")
    monkeypatch.setenv("CONSOLE_OWNER_EMAILS", "owner@example.com")
    appmod._init_auth_tables()
    appmod._init_workspace_schema()
    sent = []
    monkeypatch.setattr(
        appmod, "_send_full_report_email",
        lambda email, name, subject, body: sent.append((email, subject, body)) or ("test", None))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod, sent


def _request_link(client, sent, email="owner@example.com"):
    r = client.post("/console/login/request", data={"email": email})
    assert r.status_code == 200
    assert email not in r.get_data(as_text=True)
    assert len(sent) == 1
    return re.search(r"https?://[^\s]+token=([^\s]+)", sent[0][2]).group(1)


def test_unknown_email_gets_generic_response_and_no_email(owner_client):
    c, _, sent = owner_client
    r = c.post("/console/login/request", data={"email": "stranger@example.com"})
    assert r.status_code == 200
    assert not sent
    assert "authorized" in r.get_data(as_text=True)


def test_owner_magic_link_sets_revocable_owner_cookie(owner_client):
    c, appmod, sent = owner_client
    token = _request_link(c, sent)
    get = c.get("/console/login/verify?token=" + token)
    assert get.status_code == 200
    assert "Open console" in get.get_data(as_text=True)
    post = c.post("/console/login/verify", data={"token": token})
    assert post.status_code == 302
    assert post.headers["Location"].endswith("/console")
    cookie = next(h for h in post.headers.getlist("Set-Cookie")
                  if h.startswith(appmod.CONSOLE_COOKIE + "="))
    assert "HttpOnly" in cookie
    assert "emergency-secret" not in cookie
    # The browser session works without the shared master key.
    assert c.get("/api/console/auth-status").status_code == 200


def test_owner_link_is_single_use(owner_client):
    c, _, sent = owner_client
    token = _request_link(c, sent)
    assert c.post("/console/login/verify", data={"token": token}).status_code == 302
    assert c.post("/console/login/verify", data={"token": token}).status_code == 400


def test_owner_request_is_rate_limited_without_changing_response(owner_client):
    c, _, sent = owner_client
    first = c.post("/console/login/request", data={"email": "owner@example.com"})
    second = c.post("/console/login/request", data={"email": "owner@example.com"})
    assert first.status_code == second.status_code == 200
    assert len(sent) == 1

