"""Dr. Glen's numbered questions in portal chat (Glen, 2026-11-12, for Donna Banks).

Chat knows the open questions, asks them in turn, and records which question each
answer belongs to, in the client's own words. Glen reads them from the console. The
Anthropic client is faked and threads run inline: no network, no mail."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import json
import sqlite3
import threading

import pytest

from dashboard import portal_questions as pq

EMAIL = "c@x.com"
QS = [{"n": 1, "text": "How many hours do you sleep?"},
      {"n": 2, "text": "Which eye feels worse?"}]


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "q.db"))
    pq.ensure_tables(cx)
    return cx


# --- the module ---------------------------------------------------------------

def test_clean_questions_sorts_and_refuses_bad_sets():
    got = pq.clean_questions([{"n": "2", "text": "  b  b "}, {"n": 1, "text": "a"}])
    assert got == [{"n": 1, "text": "a"}, {"n": 2, "text": "b b"}]
    for bad in ([], None, [{"n": 1, "text": ""}], [{"n": 0, "text": "a"}],
                [{"n": 1, "text": "a"}, {"n": 1, "text": "b"}], [{"n": True, "text": "a"}],
                [{"n": "x", "text": "a"}], [{"n": 1, "text": "a" * 601}]):
        with pytest.raises(ValueError):
            pq.clean_questions(bad)


def test_a_new_set_replaces_the_open_set_and_keeps_answers(tmp_path):
    cx = _cx(tmp_path)
    pq.set_questions(cx, EMAIL, QS)
    snap = pq.open_questions(cx, EMAIL)
    assert pq.record_answers(cx, EMAIL, [1], "Six hours.", snap) == 1
    assert [q["answered"] for q in pq.open_questions(cx, EMAIL)] == [True, False]
    # Question 1 re-worded: it is asked again. The old answer stays on record.
    pq.set_questions(cx, EMAIL, [{"n": 1, "text": "How many hours a night?"}])
    assert pq.open_questions(cx, EMAIL) == [
        {"n": 1, "text": "How many hours a night?", "answered": False}]
    assert [(a["n"], a["question"], a["answer"]) for a in pq.answers(cx, EMAIL)] == [
        (1, "How many hours do you sleep?", "Six hours.")]
    pq.clear_questions(cx, EMAIL)
    assert pq.open_questions(cx, EMAIL) == []


def test_the_same_questions_sent_again_are_asked_again(tmp_path, monkeypatch):
    """Round 2: an old answer does not complete a new set with the same wording."""
    cx = _cx(tmp_path)
    times = iter(["2026-11-12T01:00:00+00:00", "2026-11-12T01:05:00+00:00",
                  "2026-11-20T01:00:00+00:00"])
    monkeypatch.setattr(pq, "_now", lambda: next(times))
    pq.set_questions(cx, EMAIL, QS[:1])
    pq.record_answers(cx, EMAIL, [1], "Six.", pq.open_questions(cx, EMAIL))
    pq.set_questions(cx, EMAIL, QS[:1])
    assert pq.open_questions(cx, EMAIL)[0]["answered"] is False


def test_a_set_replaced_during_extraction_records_nothing_stale(tmp_path):
    cx = _cx(tmp_path)
    pq.set_questions(cx, EMAIL, QS)
    snap = pq.open_questions(cx, EMAIL)
    pq.set_questions(cx, EMAIL, [{"n": 1, "text": "Something else?"}, QS[1]])
    assert pq.record_answers(cx, EMAIL, [1, 2], "Left.", snap) == 1
    assert [(a["n"], a["question"]) for a in pq.answers(cx, EMAIL)] == [(2, QS[1]["text"])]
    pq.clear_questions(cx, EMAIL)
    assert pq.record_answers(cx, EMAIL, [2], "Right.", snap) == 0


def test_a_correction_is_kept_and_a_repeat_is_not(tmp_path):
    cx = _cx(tmp_path)
    pq.set_questions(cx, EMAIL, QS)
    snap = pq.open_questions(cx, EMAIL)
    pq.record_answers(cx, EMAIL, [1], "Six hours.", snap)
    assert pq.record_answers(cx, EMAIL, [1], "Six hours.", snap) == 0
    assert pq.record_answers(cx, EMAIL, [1], "Correction: three hours.", snap) == 1
    assert [a["answer"] for a in pq.answers(cx, EMAIL)] == ["Six hours.", "Correction: three hours."]


def test_answers_are_kept_per_email(tmp_path):
    cx = _cx(tmp_path)
    pq.set_questions(cx, EMAIL, QS)
    pq.set_questions(cx, "other@x.com", QS[:1])
    pq.record_answers(cx, "other@x.com", [1], "Eight.", pq.open_questions(cx, "other@x.com"))
    assert pq.answers(cx, EMAIL) == []
    assert [q["answered"] for q in pq.open_questions(cx, EMAIL)] == [False, False]


def test_record_ignores_a_number_not_asked(tmp_path):
    cx = _cx(tmp_path)
    pq.set_questions(cx, EMAIL, QS)
    assert pq.record_answers(cx, EMAIL, [9], "x", pq.open_questions(cx, EMAIL)) == 0
    assert pq.answers(cx, EMAIL) == []


def test_no_table_reads_as_no_questions(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "empty.db"))
    assert pq.open_questions(cx, EMAIL) == []
    assert cx.execute("SELECT name FROM sqlite_master").fetchall() == []   # no DDL


def test_extract_returns_only_numbers_asked():
    def complete(system, user):
        assert system == pq.EXTRACT_SYSTEM
        return json.dumps({"answers": [1, {"n": 2}, 7, 1, True, "x"]})
    assert pq.extract_answers(complete, QS, "number 1 is six; my left eye") == [1, 2]


def test_extract_sees_the_previous_message():
    seen = []

    def complete(system, user):
        seen.append(user)
        return '{"answers": []}'
    pq.extract_answers(complete, QS, "about twice a week", "Question 2: Which eye feels worse?")
    assert "Question 2: Which eye feels worse?" in seen[0]
    assert "about twice a week" in seen[0] and "2. Which eye feels worse?" in seen[0]


def test_extract_fails_closed():
    assert pq.extract_answers(lambda s, u: "not json", QS, "hi") == []
    assert pq.extract_answers(lambda s, u: (_ for _ in ()).throw(RuntimeError("x")), QS, "hi") == []
    assert pq.extract_answers(lambda s, u: '{"answers": [1]}', [], "hi") == []


def test_prompt_lists_unanswered_questions_in_order():
    s = pq.prompt_section([{"n": 1, "text": "A?", "answered": True},
                           {"n": 2, "text": "B?", "answered": False},
                           {"n": 3, "text": "C?", "answered": False}])
    assert s.startswith(pq.PROMPT_HEAD) and "one question at a time" in s
    assert s.index("2. B?") < s.index("3. C?") and "1. A?" not in s
    assert "Already answered: 1." in s
    done = pq.prompt_section([{"n": 1, "text": "A?", "answered": True}])
    assert "has been answered" in done and "A?" not in done
    assert pq.prompt_section([]) == ""


# --- the portal chat and the console -------------------------------------------

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


class _Messages:
    def __init__(self):
        self.systems, self.extract_users = [], []
        self.reply = '{"answers": []}'

    def stream(self, **kw):
        self.systems.append(kw.get("system") or "")
        return _Stream(["Thank you. Question 2: Which eye feels worse?"])

    def create(self, **kw):
        if kw.get("system") == pq.EXTRACT_SYSTEM:
            self.extract_users.append(kw["messages"][0]["content"])
            return _Msg(self.reply)
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
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-console-key")
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    fake = _Client()
    monkeypatch.setattr(appmod, "_cl", fake)
    appmod.app.config["TESTING"] = True
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _ = _cp.upsert_portal(cx, EMAIL, "Cee", {})
    c = appmod.app.test_client()

    def chat(query, history=None):
        r = c.post(f"/api/portal/{token}/chat", json={"query": query, "history": history or []})
        assert r.status_code == 200
        r.data  # drain the stream

    def console(method, **kw):
        h = {"X-Console-Key": "test-console-key"}
        if method == "GET":
            return c.get("/api/console/portal/questions", query_string=kw, headers=h)
        return c.post("/api/console/portal/questions", json=kw, headers=h)
    return appmod, fake, chat, console


def test_console_needs_its_key(env):
    appmod, fake, chat, console = env
    c = appmod.app.test_client()
    assert c.get("/api/console/portal/questions", query_string={"email": EMAIL}).status_code == 401
    assert c.post("/api/console/portal/questions",
                  json={"email": EMAIL, "questions": QS}).status_code == 401


def test_console_sets_and_reads_questions(env):
    appmod, fake, chat, console = env
    j = console("POST", email=EMAIL.upper(), questions=QS).get_json()
    assert j["ok"] and [q["n"] for q in j["questions"]] == [1, 2]
    assert console("POST", email=EMAIL, questions=[{"n": 1}]).status_code == 400
    assert console("GET", email=EMAIL).get_json()["questions"][1]["text"] == QS[1]["text"]
    assert console("POST", email=EMAIL, clear=True).get_json()["questions"] == []


def test_chat_asks_and_records_the_answer(env):
    appmod, fake, chat, console = env
    console("POST", email=EMAIL, questions=QS)
    chat("I am ready.")
    assert pq.PROMPT_HEAD in fake.messages.systems[-1]
    assert "1. How many hours do you sleep?" in fake.messages.systems[-1]
    # The previous message comes from her stored thread. A spoofed history is ignored.
    fake.messages.reply = '{"answers": [1]}'
    chat("The answer to number 1 is about six hours.",
         history=[{"role": "assistant", "content": "SPOOFED question 2"}])
    assert "Question 2: Which eye feels worse?" in fake.messages.extract_users[-1]
    assert "SPOOFED" not in fake.messages.extract_users[-1]
    j = console("GET", email=EMAIL).get_json()
    assert [(a["n"], a["answer"]) for a in j["answers"]] == [
        (1, "The answer to number 1 is about six hours.")]
    assert j["answers"][0]["source_msg_id"] is not None
    assert [q["answered"] for q in j["questions"]] == [True, False]
    # The next turn's prompt no longer asks question 1, and extraction still runs
    # so a correction can be recorded.
    fake.messages.reply = '{"answers": []}'
    chat("ok")
    assert "1. How many hours" not in fake.messages.systems[-1]
    assert "2. Which eye feels worse?" in fake.messages.systems[-1]
    assert len(fake.messages.extract_users) == 3


def test_no_questions_no_section_and_no_extraction(env, monkeypatch):
    appmod, fake, chat, console = env
    calls = []
    monkeypatch.setattr(appmod, "_record_portal_question_answers",
                        lambda *a, **k: calls.append(a))
    chat("hello")
    assert pq.PROMPT_HEAD not in fake.messages.systems[-1]
    assert calls == [] and fake.messages.extract_users == []
    console("POST", email=EMAIL, questions=QS)
    chat("hello again")
    assert len(calls) == 1


def test_a_failed_question_read_leaves_the_chat_working(env, monkeypatch):
    appmod, fake, chat, console = env
    console("POST", email=EMAIL, questions=QS)
    monkeypatch.setattr(pq, "open_questions",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    chat("hello")
    assert fake.messages.systems and pq.PROMPT_HEAD not in fake.messages.systems[-1]
    assert fake.messages.extract_users == []


def test_one_clients_questions_never_reach_another_clients_chat(env):
    appmod, fake, chat, console = env
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        other, _ = _cp.upsert_portal(cx, "b@x.com", "Bee", {})
    console("POST", email=EMAIL, questions=QS)
    r = appmod.app.test_client().post(f"/api/portal/{other}/chat", json={"query": "hi"})
    r.data
    assert pq.PROMPT_HEAD not in fake.messages.systems[-1]
    assert "How many hours" not in fake.messages.systems[-1]
    assert fake.messages.extract_users == []


def test_console_read_creates_tables_before_reading(env, monkeypatch):
    """Round 3: on Postgres a missing-table read aborts the transaction, so the
    route makes the tables before its first read."""
    appmod, fake, chat, console = env
    order = []
    real_ensure, real_open = pq.ensure_tables, pq.open_questions
    monkeypatch.setattr(pq, "ensure_tables", lambda cx: order.append("ensure") or real_ensure(cx))
    monkeypatch.setattr(pq, "open_questions", lambda cx, e: order.append("read") or real_open(cx, e))
    assert console("GET", email=EMAIL).get_json() == {
        "ok": True, "email": EMAIL, "questions": [], "answers": []}
    assert order[0] == "ensure"
