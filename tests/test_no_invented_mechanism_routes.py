"""Route level: the request a chat route actually sends to the model carries the
no-invented-mechanism rule (review round 2 on PR #1944, 2026-10-08).

The model client and retrieval are stubbed. The stream stub records the request it
was given and then raises, which every route below catches and turns into an SSE
error event. That stops each run before logging, onboarding or follow-up calls, so
nothing reaches the network, the model, or mail.
"""
import importlib
import json
import sqlite3

import pytest

from dashboard import portal_concierge as pcz

RULE = pcz.SOURCED_PRODUCT_CLAIMS.strip()
NOTE = pcz.SOURCED_PRODUCT_NOTE


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

    # Any non-stream model call (the consent-gate classifier) answers OPEN.
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: _Resp())
    monkeypatch.setattr(app._cl.messages, "stream", _stream)
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    snippet = [_Match("AngioGenX: one capsule daily. Stop two weeks before surgery.")]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: snippet)
    monkeypatch.setattr(app, "_match_query_namespaces", lambda vec: snippet)
    monkeypatch.setattr(app, "build_product_directive",
                        lambda snippets_text="", query_text="": "PRODUCT LINK TABLE (stub)")
    monkeypatch.setattr(app, "_community_related", lambda *a, **k: [])
    app._captured = captured
    return app


def _user_turn(req):
    return req["messages"][-1]["content"]


def _post_chat(app, mode):
    r = app.app.test_client().post("/chat", json={
        "query": "What is AngioGenX and how does it help the eyes?",
        "level": "self-healing", "mode": mode,
        # A history turn skips the query_log fallback read.
        "history": [{"role": "user", "content": "hello"}],
    })
    body = r.get_data(as_text=True)
    assert r.status_code == 200, body
    assert "stub: request captured" in body, body[:500]
    assert len(app._captured) == 1
    return app._captured[0]


def test_chat_brief_sends_the_rule_in_system_and_user_turn(appmod):
    req = _post_chat(appmod, "brief")
    assert req["system"].count(RULE) == 1
    user = _user_turn(req)
    assert "For a named product, give its mechanism only as a retrieved source states it." in user
    assert NOTE in user


@pytest.mark.parametrize("logged_in", [False, True])
def test_chat_full_sends_the_rule_in_system_and_user_turn(appmod, monkeypatch, logged_in):
    # Full mode is gated for anonymous visitors, so resolve as a registered tier
    # with no email (which also skips the monthly-ceiling lookup).
    monkeypatch.setattr(appmod, "_resolve_chat_tier", lambda req, sid, email: ("registered", ""))
    if logged_in:
        monkeypatch.setattr(appmod, "get_authenticated_user",
                            lambda req: {"email": "route-test@example.invalid", "name": "T"})
    req = _post_chat(appmod, "full")
    assert req["system"].count(RULE) == 1
    user = _user_turn(req)
    assert user.count(NOTE) == 1
    # The two branches differ, so make sure each was really exercised.
    marker = "BREAK & REBUILD" if logged_in else "EXTENDED FORMAT"
    assert marker in user


def test_begin_match_sends_the_rule_in_system(appmod):
    r = appmod.app.test_client().post("/begin/match/chat", json={
        "query": "Something for my eyes", "for_whom": "me"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "stub: request captured" in body, body[:500]
    req = appmod._captured[0]
    assert req["system"].count(RULE) == 1


def test_portal_chat_sends_the_rule_in_system(appmod):
    from dashboard import client_portal
    with sqlite3.connect(appmod.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(
            cx, email="route-test@example.invalid", name="T",
            content={"layers": [{"n": 1, "title": "Eyes", "remedy": "AngioGenX"}],
                     "findings": [], "reorder_items": []})
    r = appmod.app.test_client().post(
        f"/api/portal/{token}/chat",
        data=json.dumps({"query": "What is AngioGenX for?"}),
        content_type="application/json")
    body = r.get_data(as_text=True)
    assert r.status_code == 200, body[:500]
    assert len(appmod._captured) == 1, body[:500]
    assert appmod._captured[0]["system"].count(RULE) == 1
