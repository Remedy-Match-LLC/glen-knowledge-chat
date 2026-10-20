"""The chat line Glen approved on 20 Oct 2026 for a warning sign.

It says the message was passed on, so it may appear only when a warning-sign
triage item was actually stored. Drives POST /api/portal/<token>/chat with a
fake Anthropic client, threads run inline, and the email sender stubbed: no
model, network, or real mail is touched."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import json
import sqlite3
import threading

import pytest

LINE = ("I have passed this to Dr. Glen. This chat is for supporting your "
        "self-healing as a supplement to your local health care.")
REPLY = "Thank you for telling me."


class _Txt:
    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text):
        self.content = [_Txt(text)]


class _Stream:
    def __init__(self, parts):
        self.text_stream = iter(parts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeMessages:
    def __init__(self, triage_json):
        self.triage_json = triage_json
        self.triage_calls = 0

    def stream(self, **kw):
        return _Stream([REPLY])

    def create(self, **kw):
        from dashboard import portal_triage as pt
        if kw.get("system") == pt._TRIAGE_SYSTEM:
            self.triage_calls += 1
            return _Msg(self.triage_json)
        return _Msg("{}")


class _FakeClient:
    def __init__(self, triage_json):
        self.messages = _FakeMessages(triage_json)


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    monkeypatch.setattr(appmod, "embed", lambda q: (_ for _ in ()).throw(RuntimeError("no rag")))
    monkeypatch.setattr(appmod.ash_ally, "record_turn", lambda *a, **k: None)
    monkeypatch.setattr(appmod.ash_ally, "ally_overlay", lambda *a, **k: "")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda em: None)
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    sent = []

    def fake_send(to, name, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body})
        return ("stub", None)
    monkeypatch.setattr(appmod, "_send_full_report_email", fake_send)
    appmod.app.config["TESTING"] = True

    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})

    def chat(query, triage_json='{"needs_attention": false}'):
        monkeypatch.setattr(appmod, "_cl", _FakeClient(triage_json))
        r = appmod.app.test_client().post(f"/api/portal/{token}/chat",
                                          json={"query": query})
        assert r.status_code == 200
        text = ""
        for line in r.data.decode().splitlines():
            if line.startswith("data: "):
                evt = json.loads(line[6:])
                text += evt.get("token") or ""
        return text

    return appmod, chat, sent


def _triage_rows(appmod):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in cx.execute("SELECT * FROM portal_triage ORDER BY id")]
        except sqlite3.OperationalError:
            return []


def _stored_answer(appmod):
    from dashboard import portal_chat as pc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute("SELECT * FROM portal_chat_messages ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def test_route_constant_is_glens_exact_text():
    import app as appmod
    assert appmod.PORTAL_WARNING_SIGN_LINE == LINE


def test_line_appears_once_on_a_stored_warning_sign(env):
    appmod, chat, sent = env
    text = chat("My lips are swelling after the drops.")
    assert text.count(LINE) == 1
    assert text == REPLY + "\n\n" + LINE
    rows = _triage_rows(appmod)
    assert len(rows) == 1                       # stored once, not again by the thread
    assert rows[0]["urgency"] == "high" and rows[0]["category"] == "health-concern"
    assert len(sent) == 1 and sent[0]["to"] == "drglenswartwout@gmail.com"
    assert "swelling of the lips, tongue or throat" in sent[0]["body"]


def test_line_is_in_the_persisted_assistant_message(env):
    appmod, chat, sent = env
    chat("I broke out in hives after taking it.")
    msgs = _stored_answer(appmod)
    bodies = [m.get("content") or m.get("body") or m.get("text") or "" for m in msgs]
    assert any(b.endswith(LINE) and b.count(LINE) == 1 for b in bodies), msgs


def test_line_absent_on_routine_message(env):
    appmod, chat, sent = env
    text = chat("When should I reorder my Terrain Restore?")
    assert LINE not in text
    assert _triage_rows(appmod) == []
    assert sent == []


def test_line_absent_on_a_model_only_flag(env):
    appmod, chat, sent = env
    high = json.dumps({"needs_attention": True, "category": "health-concern",
                       "urgency": "high", "summary": "worried", "recommendation": "call"})
    text = chat("I feel strange and worried about the new protocol.", triage_json=high)
    assert LINE not in text
    rows = _triage_rows(appmod)
    assert len(rows) == 1 and rows[0]["urgency"] == "high"   # the model flag still lands
    assert len(sent) == 1


def test_line_absent_when_the_insert_fails(env, monkeypatch):
    appmod, chat, sent = env
    from dashboard import portal_triage as pt

    def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(pt, "add_item", broken)
    text = chat("My lips are swelling after the drops.")
    assert LINE not in text
    assert text == REPLY


def test_model_error_with_a_warning_sign_is_stored_and_emailed(env, monkeypatch):
    """Direct call of the background job: the model call raises, the backstop
    still stores the item and emails Glen (through the stub only)."""
    appmod, chat, sent = env

    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("provider down")
    monkeypatch.setattr(appmod, "_cl", Boom())
    appmod._triage_portal_message("c@x.com", "Cee", "My baby has a high fever.", "ok")
    rows = _triage_rows(appmod)
    assert len(rows) == 1
    assert rows[0]["urgency"] == "high" and rows[0]["category"] == "health-concern"
    assert len(sent) == 1
    assert "a high or lasting fever" in sent[0]["body"]
    assert "[Portal triage · high]" in sent[0]["subject"]
