"""A visitor who opts in on the begin page ("Remember me": email + Terms) must get the
remedy email for the match the chat names. Reproduced on prod 2026-10-07: the match card
showed, nothing queued, only GHL's free-course welcome arrived. The session's consented
email addresses the queue only; it never loads that person's intake into the chat."""
import json
import os

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import app


class _FakeStream:
    text_stream = iter(["Clear the Way fits what you describe."])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeMessages:
    def stream(self, **kw):
        s = _FakeStream()
        s.text_stream = iter(["Clear the Way fits what you describe."])
        return s

    def create(self, **kw):
        class _C:
            text = json.dumps({"matched": True, "name": "Clear the Way", "kind": "formulation"})
        class _R:
            content = [_C()]
        return _R()


class _FakeClient:
    messages = _FakeMessages()


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    import begin_funnel
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:
        begin_funnel.init_journey_tables(cx)
        cx.execute("CREATE TABLE IF NOT EXISTS people (name TEXT, email TEXT)")
    monkeypatch.setattr(app, "ghl_onboard_contact", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: None)
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-test-1", domain="localhost")
    return c


def _chat_rig(monkeypatch):
    """Stub retrieval and the model; record what the route queues and loads."""
    rec = {"queued": [], "context": []}
    monkeypatch.setattr(app, "_velocity_guard", lambda *a, **k: None)
    monkeypatch.setattr(app, "embed", lambda text: [0.0])
    monkeypatch.setattr(app, "_match_query_namespaces", lambda vec: [])
    monkeypatch.setattr(app, "_is_gated_question", lambda q: False)
    monkeypatch.setattr(app, "_cl", _FakeClient())
    monkeypatch.setattr(app, "_member_context_for_email", lambda e: rec["context"].append(e) or {})
    monkeypatch.setattr(app, "_email_remedy_match_once",
                        lambda email, name, sid, match, proven=False:
                        rec["queued"].append((email, match["name"], proven)))
    return rec


def _turn(c):
    return c.post("/begin/match/chat", json={"query": "dry eyes", "email": ""}).get_data(as_text=True)


def test_an_opted_in_visitor_gets_the_match_queued_to_their_email(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    rec = _chat_rig(monkeypatch)
    assert '"match"' not in _turn(c)          # not a member yet: no match, nothing queued
    assert rec["queued"] == []
    r = c.post("/begin/unlock", json={"trigger": "tos", "email": "Visitor@Example.com",
                                      "tos": True, "first_name": "Vee"})
    assert r.status_code == 200 and r.get_json()["tos_agreed_at"]
    body = _turn(c)
    assert '"match"' in body
    # Not signed in: queued, but as unproven, so it waits for confirmation.
    assert rec["queued"] == [("visitor@example.com", "Clear the Way", False)]


def test_the_consented_email_never_loads_personal_context(monkeypatch, tmp_path):
    """Shared browser: the next person on this session must not get the first one's intake."""
    c = _client(monkeypatch, tmp_path)
    rec = _chat_rig(monkeypatch)
    c.post("/begin/unlock", json={"trigger": "tos", "email": "a@example.com", "tos": True})
    _turn(c)
    assert rec["queued"] and rec["context"] == []
    assert "a@example.com" not in _turn(c)


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


def test_a_signed_in_client_skips_confirmation(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    rec = _chat_rig(monkeypatch)
    monkeypatch.setattr(app, "get_authenticated_user", lambda req: {"email": "Client@Example.com"})
    c.post("/begin/unlock", json={"trigger": "tos", "email": "client@example.com", "tos": True})
    _turn(c)
    email, product, proven = rec["queued"][-1]
    assert (email.lower(), product, proven) == ("client@example.com", "Clear the Way", True)
