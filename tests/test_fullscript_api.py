"""Fullscript API: Glen's OAuth and catalog search (Glen, 2026-10-18).

The token is stored encrypted in the database (Glen: "yes"). Fullscript rotates the
refresh token, so a refresh is committed before its access token is used. Search
keeps the relevance filter and the never-recommend list, and marks herbal unchecked.
No test reaches Fullscript: every HTTP call is a stub."""
import importlib
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from dashboard import fullscript_api as fsa

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FULLSCRIPT_SANDBOX_CLIENT_ID", "cid")
    monkeypatch.setenv("FULLSCRIPT_SANDBOX_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("FULLSCRIPT_TOKEN_KEY", KEY)


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def _poster(calls, access="A2", refresh="R2", status=200, created=None):
    def post(url, json=None, timeout=None):
        calls.append((url, dict(json)))
        return _Resp(status, {"oauth": {"access_token": access, "refresh_token": refresh,
                                        "expires_in": 7200,
                                        "created_at": created or 1_000_000}})
    return post


def _raw(cx):
    return cx.execute("SELECT sealed FROM fullscript_oauth_token").fetchone()[0]


def test_the_authorize_url_names_the_sandbox_scope_callback_and_state():
    url = fsa.authorization_url("st")
    assert url.startswith("https://api-us-snd.fullscript.io/api/oauth/authorize?")
    q = parse_qs(urlparse(url).query)
    assert q == {"client_id": ["cid"], "redirect_uri": [fsa.REDIRECT_URI],
                 "response_type": ["code"], "scope": ["catalog:read"], "state": ["st"]}
    assert fsa.REDIRECT_URI == "https://illtowell.com/fullscript/oauth/callback"


def test_a_code_is_exchanged_and_the_stored_token_is_encrypted(cx):
    calls = []
    fsa.exchange_code(cx, "CODE", post=_poster(calls, "ACCESS1", "REFRESH1"))
    url, body = calls[0]
    assert url == "https://api-us-snd.fullscript.io/api/oauth/token"
    assert body == {"grant_type": "authorization_code", "code": "CODE", "client_id": "cid",
                    "client_secret": "csecret", "redirect_uri": fsa.REDIRECT_URI}
    sealed = _raw(cx)
    assert "ACCESS1" not in sealed and "REFRESH1" not in sealed
    assert fsa.connected(cx)


def test_a_fresh_token_is_used_without_a_refresh(cx):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=1_000_000))
    calls = []
    assert fsa.access_token(cx, post=_poster(calls), now=1_000_000 + 7200 - fsa.EARLY - 1) == "A1"
    assert calls == []


def test_a_token_near_expiry_refreshes_and_the_new_pair_is_committed_first(cx, tmp_path):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=1_000_000))
    calls = []
    got = fsa.access_token(cx, post=_poster(calls, "A2", "R2"), now=1_000_000 + 7200 - fsa.EARLY)
    assert got == "A2"
    assert calls[0][1]["grant_type"] == "refresh_token" and calls[0][1]["refresh_token"] == "R1"
    # Committed: a second connection sees the rotated refresh token.
    other = sqlite3.connect(str(tmp_path / "chat_log.db"))
    stored = fsa._unseal(other.execute("SELECT sealed FROM fullscript_oauth_token").fetchone()[0])
    other.close()
    assert (stored["access_token"], stored["refresh_token"]) == ("A2", "R2")


def test_when_another_worker_refreshed_first_its_token_is_used(cx):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=1_000_000))

    def racing_post(url, json=None, timeout=None):
        # Another worker stores its refresh while this one waits on Fullscript.
        fsa.save_new(cx, {"access_token": "A3", "refresh_token": "R3", "expires_at": 9e9})
        return _Resp(200, {"access_token": "A2", "refresh_token": "R2", "expires_in": 7200})

    assert fsa.access_token(cx, post=racing_post, now=2_000_000) == "A3"
    assert fsa._unseal(_raw(cx))["refresh_token"] == "R3"


def test_a_failed_refresh_keeps_the_old_token_and_never_echoes_the_body(cx):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=1_000_000))
    before = _raw(cx)

    def failing(url, json=None, timeout=None):
        return _Resp(401, {"error": "invalid_grant", "refresh_token": "LEAK"})
    with pytest.raises(RuntimeError) as err:
        fsa.access_token(cx, post=failing, now=2_000_000)
    assert "401" in str(err.value) and "LEAK" not in str(err.value)
    assert _raw(cx) == before


def test_without_the_key_nothing_is_stored_or_read(cx, monkeypatch):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1"))
    monkeypatch.delenv("FULLSCRIPT_TOKEN_KEY")
    assert fsa.ready() == ["FULLSCRIPT_TOKEN_KEY is not in Doppler."]
    assert not fsa.connected(cx)
    with pytest.raises(fsa.NotConnected):
        fsa.access_token(cx)
    with pytest.raises(fsa.NotConnected):
        fsa.exchange_code(cx, "C", post=_poster([]))


def test_a_different_key_cannot_read_the_stored_token(cx, monkeypatch):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1"))
    monkeypatch.setenv("FULLSCRIPT_TOKEN_KEY", Fernet.generate_key().decode())
    assert not fsa.connected(cx)
    with pytest.raises(fsa.NotConnected):
        fsa.access_token(cx)


def test_ready_names_missing_credentials(monkeypatch):
    assert fsa.ready() == []
    monkeypatch.delenv("FULLSCRIPT_SANDBOX_CLIENT_SECRET")
    assert fsa.ready() == ["The Fullscript sandbox client ID or secret is not in Doppler."]


def _getter(products, seen):
    def get(url, params=None, headers=None, timeout=None):
        seen.append((url, params, headers))
        return _Resp(200, {"products": products})
    return get


def _product(name, brand="Brand"):
    return {"id": "p-" + name, "name": name, "brand": {"name": brand},
            "primary_variant": {"availability": "In Stock", "msrp": "19.99"},
            "status": "active"}


def test_search_keeps_relevant_allowed_hits_and_lists_the_dropped(cx):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=9e9))
    seen = []
    out = fsa.search(cx, "magnesium glycinate", get=_getter([
        _product("Magnesium Glycinate"), _product("Mag Complex"),
        _product("Fungifuge"), _product("Fungifuge Capsules")], seen))
    url, params, headers = seen[0]
    assert url == "https://api-us-snd.fullscript.io/api/catalog/search/products"
    assert params == {"query": "magnesium glycinate"}
    assert headers == {"Authorization": "Bearer A1"}
    assert [r["name"] for r in out["results"]] == ["Magnesium Glycinate"]
    assert out["results"][0]["herbal"] == "unchecked"
    assert [(d["name"], d["why"]) for d in out["dropped"]] == [
        ("Mag Complex", "no search word in its name"),
        ("Fungifuge", "no search word in its name"),
        ("Fungifuge Capsules", "no search word in its name")]


def test_a_never_recommend_product_is_dropped_even_when_relevant(cx):
    fsa.exchange_code(cx, "C", post=_poster([], "A1", "R1", created=9e9))
    out = fsa.search(cx, "fungifuge", get=_getter([_product("Fungifuge Capsules"),
                                                   _product("Fungifuged Blend")], []))
    assert out["results"] == []
    assert [d["why"] for d in out["dropped"]] == ["on the never-recommend list",
                                                  "no search word in its name"]


def test_a_search_with_no_word_of_four_letters_is_refused(cx):
    with pytest.raises(ValueError):
        fsa.search(cx, "B12 D3", get=_getter([], []))


def test_search_without_a_connection_raises_not_connected(cx):
    with pytest.raises(fsa.NotConnected):
        fsa.search(cx, "magnesium", get=_getter([], []))


# Routes.

def _app(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        app = importlib.import_module("app")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"app module not importable: {exc}")
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    return app


OWNER = {"X-Console-Key": "testkey"}


def test_routes_refuse_anyone_but_the_owner(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).app.test_client()
    for path in ("/fullscript/oauth/start", "/fullscript/oauth/callback?code=c&state=s",
                 "/api/console/fullscript/api-status",
                 "/api/console/fullscript/catalog-search?q=magnesium"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"X-Console-Key": "wrong"}).status_code == 401, path


def test_an_unset_console_secret_does_not_open_the_routes(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "")
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    client = app.app.test_client()
    assert client.get("/fullscript/oauth/start").status_code == 401
    assert client.get("/api/console/fullscript/catalog-search?q=magnesium").status_code == 401


def test_start_then_callback_connects_once(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = app.app.test_client()
    resp = client.get("/fullscript/oauth/start", headers=OWNER)
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["Location"]).query)["state"][0]
    calls = []
    monkeypatch.setattr(fsa.requests, "post", _poster(calls, "A1", "R1"))
    ok = client.get(f"/fullscript/oauth/callback?code=CODE&state={state}", headers=OWNER)
    assert ok.status_code == 200 and b"Fullscript is connected" in ok.data
    assert calls[0][1]["code"] == "CODE"
    again = client.get(f"/fullscript/oauth/callback?code=CODE&state={state}", headers=OWNER)
    assert again.status_code == 400 and len(calls) == 1
    status = client.get("/api/console/fullscript/api-status", headers=OWNER).get_json()
    assert status == {"environment": "sandbox", "missing": [], "connected": True}
    assert b"A1" not in client.get("/api/console/fullscript/api-status", headers=OWNER).data


def test_a_state_this_app_never_issued_is_refused(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(fsa.requests, "post", _poster(calls))
    resp = app.app.test_client().get("/fullscript/oauth/callback?code=C&state=forged",
                                     headers=OWNER)
    assert resp.status_code == 400 and calls == []


def test_start_says_what_is_missing_instead_of_redirecting(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.delenv("FULLSCRIPT_TOKEN_KEY")
    resp = app.app.test_client().get("/fullscript/oauth/start", headers=OWNER)
    assert resp.status_code == 503 and b"FULLSCRIPT_TOKEN_KEY is not in Doppler." in resp.data


def test_search_route_asks_to_connect_when_not_connected(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    resp = app.app.test_client().get("/api/console/fullscript/catalog-search?q=magnesium",
                                     headers=OWNER)
    assert resp.status_code == 409 and resp.get_json()["connect"] == "/fullscript/oauth/start"
