"""Every chat route sends the CDS standing rule in its system prompt (review of #2143).

Only /chat carried a CDS rule, and only when the user's own words named it. The concierge,
match, portal and practitioner routes retrieve the same product snippets. Glen, 2026-10-25:
CDS is sold "a water purifier only". Stubbed as in test_no_invented_mechanism_routes.py:
the stream stub records the request and raises, so nothing reaches the model or mail.
"""
import importlib
import json
import sqlite3

import pytest


class _Stop(Exception):
    pass


class _Match:
    def __init__(self, text):
        self.metadata = {"text": text, "source": "test", "url": ""}
        self.score = 0.9
        self.id = "t1"


class _Resp:
    class _C:
        text = "OPEN"
    content = [_C()]


@pytest.fixture()
def appmod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app
    importlib.reload(app)
    app.app.config["TESTING"] = True
    captured = []

    def _stream(**kw):
        captured.append(kw)
        raise _Stop("stub: request captured")

    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: _Resp())
    monkeypatch.setattr(app._cl.messages, "stream", _stream)
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    # An old snippet that tells the reader to drink it: the rule must arrive anyway.
    snippet = [_Match("CDS: 3 drops of each in 8 ounces of water, 3 times a day.")]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: snippet)
    monkeypatch.setattr(app, "_match_query_namespaces", lambda vec: snippet)
    monkeypatch.setattr(app, "build_product_directive",
                        lambda snippets_text="", query_text="": "PRODUCT LINK TABLE (stub)")
    monkeypatch.setattr(app, "_community_related", lambda *a, **k: [])
    app._captured = captured
    return app


def _one(app, r):
    body = r.get_data(as_text=True)
    assert r.status_code == 200, body[:500]
    assert len(app._captured) == 1, body[:500]
    return app._captured[0]


def _rule(app):
    return app._STANDING_USE_RULE


def test_the_rule_says_what_glen_ruled(appmod):
    r = _rule(appmod)
    assert "is sold as a water purifier only" in r and "never say to drink" in r
    assert "price" in r and "oxygen source" in r and "any snippet" in r


def test_chat_sends_it(appmod):
    req = _one(appmod, appmod.app.test_client().post("/chat", json={
        "query": "What helps with parasites?", "level": "self-healing", "mode": "brief",
        "history": [{"role": "user", "content": "hello"}]}))
    assert req["system"].count(_rule(appmod)) == 1


def test_begin_match_sends_it(appmod):
    req = _one(appmod, appmod.app.test_client().post("/begin/match/chat", json={
        "query": "Something for parasites", "for_whom": "me"}))
    assert req["system"].count(_rule(appmod)) == 1


def test_concierge_sends_it(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "is_member", lambda sid, email: True)
    req = _one(appmod, appmod.app.test_client().post("/begin/concierge/chat", json={
        "query": "How do I use what I bought?"}))
    assert req["system"].count(_rule(appmod)) == 1


def test_portal_chat_sends_it(appmod):
    from dashboard import client_portal
    with sqlite3.connect(appmod.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(
            cx, email="route-test@example.invalid", name="T",
            content={"layers": [{"n": 1, "title": "Gut", "remedy": "Para-Cleanse"}],
                     "findings": [], "reorder_items": []})
    req = _one(appmod, appmod.app.test_client().post(
        f"/api/portal/{token}/chat", data=json.dumps({"query": "How much CDS do I drink?"}),
        content_type="application/json"))
    assert req["system"].count(_rule(appmod)) == 1


def test_practitioner_assist_sends_it(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: 1)
    req = _one(appmod, appmod.app.test_client().post("/api/practitioner/assist", json={
        "query": "What helps a patient with parasites?"}))
    assert req["system"].count(_rule(appmod)) == 1
