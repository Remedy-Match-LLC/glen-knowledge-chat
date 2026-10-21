"""app.py wiring of the health-suggestion extractor into the portal chat: gated
by PORTAL_HEALTH_PROFILE_ENABLED, model sees the client's message only. The
Anthropic client is faked; threads run inline; no network, no mail."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import json
import sqlite3
import threading

import pytest

from dashboard import health_suggestions as hs


class _Txt:
    def __init__(self, t):
        self.text = t


class _Msg:
    def __init__(self, t):
        self.content = [_Txt(t)]


class _Stream:
    def __init__(self, parts):
        self.text_stream = iter(parts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ANSWER = "Warfarin is a blood thinner; ANSWER-ONLY-TEXT."


class _Messages:
    def __init__(self):
        self.extract_users = []

    def stream(self, **kw):
        return _Stream([ANSWER])

    def create(self, **kw):
        if kw.get("system") == hs.EXTRACT_SYSTEM:
            user = kw["messages"][0]["content"]
            self.extract_users.append(user)
            return _Msg(json.dumps({"items": [{
                "field_id": "medications", "value": {"medication": "Warfarin"},
                "quote": "I take warfarin", "about": "self"}]}))
        return _Msg('{"needs_attention": false}')


class _Client:
    def __init__(self):
        self.messages = _Messages()


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "embed", lambda q: (_ for _ in ()).throw(RuntimeError("no rag")))
    monkeypatch.setattr(appmod.ash_ally, "record_turn", lambda *a, **k: None)
    monkeypatch.setattr(appmod.ash_ally, "ally_overlay", lambda *a, **k: "")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda em: None)
    monkeypatch.setattr(appmod, "_send_full_report_email", lambda *a, **k: ("stub", None))
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    fake = _Client()
    monkeypatch.setattr(appmod, "_cl", fake)
    appmod.app.config["TESTING"] = True
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})

    def chat(query):
        r = appmod.app.test_client().post(f"/api/portal/{token}/chat", json={"query": query})
        assert r.status_code == 200
        r.data  # drain the stream
    return appmod, fake, chat


def _pending(appmod):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        return hs.list_pending(cx, "c@x.com")


def test_flag_on_chat_turn_queues_a_suggestion(env, monkeypatch):
    appmod, fake, chat = env
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    chat("I take warfarin every day.")
    assert fake.messages.extract_users == ["I take warfarin every day."]
    p = _pending(appmod)
    assert len(p) == 1 and p[0]["field_id"] == "medications" and p[0]["source"] == "chat"
    assert json.loads(p[0]["suggested_value"]) == {"medication": "Warfarin"}


def test_extractor_never_sees_the_answer(env, monkeypatch):
    appmod, fake, chat = env
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    chat("I take warfarin every day.")
    assert all("ANSWER-ONLY-TEXT" not in u for u in fake.messages.extract_users)


def test_repeat_mention_does_not_stack_a_pending_duplicate(env, monkeypatch):
    appmod, fake, chat = env
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    chat("I take warfarin every day.")
    chat("Like I said, I take warfarin.")
    assert len(_pending(appmod)) == 1


def test_flag_off_never_calls_the_extractor(env, monkeypatch):
    appmod, fake, chat = env
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    chat("I take warfarin every day.")
    assert fake.messages.extract_users == []
    assert _pending(appmod) == []


def test_background_job_is_gated_itself(env, monkeypatch):
    appmod, fake, chat = env
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    assert appmod._extract_portal_health_suggestions("c@x.com", "I take warfarin.", "") == []
    assert fake.messages.extract_users == []


def test_flag_off_chat_never_starts_the_extraction_job(env, monkeypatch):
    appmod, fake, chat = env
    calls = []
    monkeypatch.setattr(appmod, "_extract_portal_health_suggestions",
                        lambda *a, **k: calls.append(a))
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    chat("I take warfarin every day.")
    assert calls == []
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    chat("I take warfarin every day.")
    assert len(calls) == 1 and calls[0][1] == "I take warfarin every day."
