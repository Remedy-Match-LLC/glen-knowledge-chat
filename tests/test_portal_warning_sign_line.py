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


EVENTS = []


class _FakeMessages:
    def __init__(self, triage_json, stream_error=None):
        self.triage_json = triage_json
        self.triage_calls = 0
        self.stream_error = stream_error

    def stream(self, **kw):
        if self.stream_error:
            raise self.stream_error
        return _Stream([REPLY, " More."])

    def create(self, **kw):
        from dashboard import portal_triage as pt
        if kw.get("system") == pt._TRIAGE_SYSTEM:
            self.triage_calls += 1
            EVENTS.append("classify")
            return _Msg(self.triage_json)
        return _Msg("{}")


class _FakeClient:
    def __init__(self, triage_json, stream_error=None):
        self.messages = _FakeMessages(triage_json, stream_error)
        self.options = []

    def with_options(self, **kw):
        self.options.append(kw)
        return self


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
    EVENTS.clear()
    send_result = {"value": ("gmail-api", None)}

    def fake_send(to, name, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "kw": kw})
        EVENTS.append("send")
        if isinstance(send_result["value"], Exception):
            raise send_result["value"]
        return send_result["value"]
    appmod._test_send_result = send_result
    monkeypatch.setattr(appmod, "_send_full_report_email", fake_send)
    appmod.app.config["TESTING"] = True

    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})

    def chat(query, triage_json='{"needs_attention": false}', stream_error=None):
        monkeypatch.setattr(appmod, "_cl", _FakeClient(triage_json, stream_error))
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
    assert text == REPLY + " More." + "\n\n" + LINE
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
    monkeypatch.setattr(pt, "add_warning_item", broken)
    text = chat("My lips are swelling after the drops.")
    assert LINE not in text
    assert text == REPLY + " More."


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


# --- delivery does not depend on the stream (review rounds 1 and 2) ------------

def test_disconnect_after_first_chunk_still_stores_and_emails(env, monkeypatch):
    appmod, chat, sent = env
    monkeypatch.setattr(appmod, "_cl", _FakeClient('{"needs_attention": false}'))
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        token = _cp.ensure_token(cx, "c@x.com", "Cee")
    r = appmod.app.test_client().post(f"/api/portal/{token}/chat",
                                      json={"query": "My lips are swelling!"}, buffered=False)
    first = next(iter(r.response))
    assert first
    r.close()                                   # the client goes away mid-stream
    rows = _triage_rows(appmod)
    assert len(rows) == 1 and rows[0]["urgency"] == "high"
    assert len(sent) == 1


def test_stream_error_still_stores_and_emails_once(env):
    appmod, chat, sent = env
    text = chat("My lips are swelling after the drops.", stream_error=RuntimeError("down"))
    assert LINE not in text
    assert len(_triage_rows(appmod)) == 1
    assert len(sent) == 1


def test_email_goes_before_the_model_is_asked(env):
    appmod, chat, sent = env
    chat("My lips are swelling after the drops.")
    assert "send" in EVENTS
    assert "classify" not in EVENTS or EVENTS.index("send") < EVENTS.index("classify")


def test_enrichment_model_call_is_short_and_without_retries(env, monkeypatch):
    appmod, chat, sent = env
    fake = _FakeClient(json.dumps({"needs_attention": True, "category": "other",
                                   "urgency": "low", "summary": "lip swelling",
                                   "recommendation": "wait for next visit"}))
    monkeypatch.setattr(appmod, "_cl", fake)
    appmod._warning_email_job(_store(appmod), "c@x.com", "Cee", "My lips are swelling.",
                              ["swelling"])
    assert fake.options == [{"timeout": 20.0, "max_retries": 0}]
    row = _triage_rows(appmod)[0]
    assert row["summary"].endswith("lip swelling")             # enriched
    from dashboard import portal_triage as pt
    assert row["recommendation"] == pt.WARNING_RECOMMENDATION   # never the model's


def _store(appmod, msg="My lips are swelling.", keys=("swelling",)):
    item_id, created = appmod._store_warning_sign_flag("c@x.com", "Cee", msg, list(keys))
    assert created
    return item_id


def test_retry_of_the_same_message_is_not_stored_or_emailed_twice(env):
    appmod, chat, sent = env
    t1 = chat("My lips are swelling after the drops.")
    t2 = chat("my lips are  swelling after the drops")
    assert LINE in t1 and LINE in t2           # the item exists, so the line is true
    assert len(_triage_rows(appmod)) == 1
    assert len(sent) == 1


@pytest.mark.parametrize("result,status,emailed", [
    (("gmail-api", None), "sent:gmail-api", True),
    (("console-log", "no email-send mechanism configured"), "unsent:console-log", False),
    (("suppressed", None), "unsent:suppressed", False),
    (RuntimeError("smtp down"), "unsent:error", False),
])
def test_delivery_status_is_recorded(env, result, status, emailed):
    appmod, chat, sent = env
    appmod._test_send_result["value"] = result
    chat("My lips are swelling after the drops.")
    row = _triage_rows(appmod)[0]
    assert row["email_status"] == status
    assert bool(row["emailed_at"]) is emailed
    assert row["email_attempts"] == 1
    assert sent[0]["kw"].get("log_body") is False


def test_resend_job_retries_an_unsent_item_once(env):
    appmod, chat, sent = env
    appmod._test_send_result["value"] = ("console-log", "x")
    chat("My lips are swelling after the drops.")
    with sqlite3.connect(appmod.LOG_DB) as cx:     # pretend the last try was 20 min ago
        cx.execute("UPDATE portal_triage SET email_last_try='2000-01-01T00:00:00Z'")
    appmod._test_send_result["value"] = ("gmail-api", None)
    assert appmod._resend_unsent_triage_emails() == 1
    assert appmod._resend_unsent_triage_emails() == 0   # delivered, nothing left
    row = _triage_rows(appmod)[0]
    assert row["email_status"] == "sent:gmail-api" and row["email_attempts"] == 2
    assert len(sent) == 2


def test_resend_skips_recent_resolved_and_untracked_items(env):
    appmod, chat, sent = env
    appmod._test_send_result["value"] = ("console-log", "x")
    chat("My lips are swelling after the drops.")
    assert appmod._resend_unsent_triage_emails() == 0       # tried under 15 min ago
    from datetime import datetime, timedelta, timezone
    five_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE portal_triage SET email_last_try=?", (five_ago,))
    assert appmod._resend_unsent_triage_emails() == 0       # 5 minutes is still too soon
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE portal_triage SET email_last_try='2000-01-01T00:00:00Z', resolved=1")
    assert appmod._resend_unsent_triage_emails() == 0       # resolved
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE portal_triage SET resolved=0, email_status=NULL")
    assert appmod._resend_unsent_triage_emails() == 0       # legacy row, never tracked
    assert len(sent) == 1


def test_resend_is_registered_on_the_scheduler():
    import app as appmod, inspect
    src = inspect.getsource(appmod._start_scheduler)
    assert "_resend_unsent_triage_emails" in src


def test_console_log_fallback_keeps_the_body_out_of_the_log(env, monkeypatch, capsys):
    appmod, chat, sent = env
    monkeypatch.setattr(appmod, "_send_full_report_email", _REAL_SEND)
    import dashboard.inbox as inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no gmail")))
    from dashboard import ghl_email
    monkeypatch.setattr(ghl_email, "is_configured", lambda: False)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    via, _ = appmod._send_full_report_email("x@y.com", "X", "Subj", "SECRET-HEALTH-TEXT",
                                            log_body=False)
    out = capsys.readouterr().out
    assert via == "console-log"
    assert "SECRET-HEALTH-TEXT" not in out and "Subj" in out


import app as _app_for_real_send  # noqa: E402
_REAL_SEND = _app_for_real_send._send_full_report_email


def test_resend_sends_nothing_when_another_worker_claimed_it(env, monkeypatch):
    appmod, chat, sent = env
    appmod._test_send_result["value"] = ("console-log", "x")
    chat("My lips are swelling after the drops.")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.execute("UPDATE portal_triage SET email_last_try='2000-01-01T00:00:00Z'")
    from dashboard import portal_triage as pt
    monkeypatch.setattr(pt, "claim_resend", lambda *a, **k: False)
    assert appmod._resend_unsent_triage_emails() == 0
    assert len(sent) == 1
