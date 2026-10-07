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
