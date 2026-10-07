"""A visitor who opts in on the begin page ("Remember me": email + Terms) must reach the
chat turn with that email, so the remedy email can queue. Reproduced on prod 2026-10-07:
the match card showed, no remedy email queued, only GHL's free-course welcome arrived."""
import os

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    import begin_funnel
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:
        begin_funnel.init_journey_tables(cx)
    monkeypatch.setattr(app, "ghl_onboard_contact", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: None)
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-test-1", domain="localhost")
    return c


def test_an_opted_in_session_carries_its_email_into_the_chat(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post("/begin/unlock", json={"trigger": "tos", "email": "Visitor@Example.com",
                                      "tos": True, "first_name": "Vee"})
    assert r.status_code == 200 and r.get_json()["tos_agreed_at"]
    with app.app.test_request_context("/begin/match/chat", method="POST"):
        from flask import request
        tier, email = app._begin_chat_identity(request, "sess-test-1", "")
    assert email == "visitor@example.com"


def test_a_session_without_terms_gets_no_email(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post("/begin/unlock", json={"trigger": "email", "email": "nope@example.com",
                                      "tos": False, "first_name": "No"})
    assert r.status_code == 200 and r.get_json()["email"] == "nope@example.com"
    assert not r.get_json().get("tos_agreed_at")
    assert app._consented_session_email("sess-test-1") == ""


def test_another_session_does_not_borrow_the_email(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c.post("/begin/unlock", json={"trigger": "tos", "email": "a@example.com", "tos": True})
    assert app._consented_session_email("sess-test-1") == "a@example.com"
    assert app._consented_session_email("someone-else") == ""
    assert app._consented_session_email("") == ""


def test_an_email_in_the_request_wins(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c.post("/begin/unlock", json={"trigger": "tos", "email": "a@example.com", "tos": True})
    with app.app.test_request_context("/begin/match/chat", method="POST"):
        from flask import request
        assert app._begin_chat_identity(request, "sess-test-1", "b@example.com")[1] == "b@example.com"


def test_the_chat_route_uses_the_consented_identity(monkeypatch, tmp_path):
    """Drives /begin/match/chat itself, with retrieval and the model stubbed out. The
    route looks up the member's context by email before any model call, so recording
    that lookup shows which email the route resolved."""
    c = _client(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(app, "_velocity_guard", lambda *a, **k: None)
    monkeypatch.setattr(app, "embed", lambda text: [0.0])
    monkeypatch.setattr(app, "_match_query_namespaces", lambda vec: [])
    monkeypatch.setattr(app, "_member_context_for_email", lambda e: seen.append(e) or {})
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:     # the household check reads this table first
        cx.execute("CREATE TABLE IF NOT EXISTS people (name TEXT, email TEXT)")

    class _Stop:
        def __getattr__(self, name):
            raise RuntimeError("model call stubbed out")
    monkeypatch.setattr(app, "_cl", _Stop())

    def turn():
        try:
            c.post("/begin/match/chat", json={"query": "hi", "email": ""}).get_data()
        except Exception:
            pass

    turn()
    assert seen == []                       # a new visitor has no email
    c.post("/begin/unlock", json={"trigger": "tos", "email": "v@example.com", "tos": True})
    turn()
    assert seen == ["v@example.com"]
