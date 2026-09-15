"""One consent check for every app sender.

people-48's field spec, 2026-09-15. `email_suppression.is_suppressed` blocks when
EITHER store says stop:
  (a) the address has any row in email_suppression, whatever its bounce_type, or
  (b) a People hub person with that email carries the exact `consent:unsubscribed` tag.

Before this, only (a) was read, so an opt-out recorded only in the hub reached nobody.

The send-path tests seed a person tagged consent:unsubscribed with NO email_suppression
row, replace the real transport with a recorder, and assert two things: the check was
reached and returned True, and the transport was never called. Each also sends to a
clean address afterwards, which proves the recorder would have caught a send.
"""
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import db, email_suppression as es, ghl_email, inbox  # noqa: E402

BLOCKED = "left@example.com"
CLEAN = "stays@example.com"


def _people_table(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS people ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, "
               "tags TEXT DEFAULT '[]')")


def _person(cx, email, tags):
    _people_table(cx)
    cx.execute("INSERT INTO people (email, tags) VALUES (?, ?)", (email, json.dumps(tags)))
    cx.commit()


def _cx():
    cx = sqlite3.connect(":memory:")
    es.init_table(cx)
    _people_table(cx)
    return cx


@pytest.fixture
def spy(monkeypatch):
    """Wrap the real check. Records (email, result) so a test can prove it ran."""
    real = es.is_suppressed
    seen = []

    def _spy(cx, email):
        out = real(cx, email)
        seen.append((email, out))
        return out

    monkeypatch.setattr(es, "is_suppressed", _spy)
    return seen


# ── is_suppressed itself ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bounce_type", ["hard", "optout", "ghl-dnd", "anything-else"])
def test_a_table_row_blocks_whatever_its_bounce_type(bounce_type):
    cx = _cx()
    es.add(cx, BLOCKED, bounce_type, "reason", "test")
    assert es.is_suppressed(cx, BLOCKED) is True


def test_the_exact_hub_tag_blocks_with_no_table_row():
    cx = _cx()
    _person(cx, BLOCKED, ["type:client", "consent:unsubscribed"])
    assert cx.execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0] == 0
    assert es.is_suppressed(cx, BLOCKED) is True
    assert es.is_suppressed(cx, "  Left@Example.COM ") is True


@pytest.mark.parametrize("tag", [
    "consent:sms-unsubscribed",     # a text opt-out does not gate email
    "consent:unsubscribed-review",  # contains the whole tag, is not the tag
    "unsubscribed on sms",
])
def test_a_tag_that_only_resembles_the_hub_tag_does_not_block(tag):
    cx = _cx()
    _person(cx, CLEAN, ["type:client", tag])
    assert es.is_suppressed(cx, CLEAN) is False


def test_a_clean_address_is_not_suppressed():
    cx = _cx()
    _person(cx, CLEAN, ["type:client", "consent:opted-in"])
    assert es.is_suppressed(cx, CLEAN) is False
    assert es.is_suppressed(cx, "nobody@example.com") is False
    assert es.is_suppressed(cx, "") is False
    assert es.is_suppressed(cx, None) is False


def test_a_connection_without_a_people_table_still_reads_the_table():
    cx = sqlite3.connect(":memory:")
    es.init_table(cx)
    es.add(cx, BLOCKED, "hard", "NXDOMAIN", "test")
    assert es.is_suppressed(cx, BLOCKED) is True
    assert es.is_suppressed(cx, CLEAN) is False


# ── every real send path ─────────────────────────────────────────────────────

def _app():
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    import app  # a failed import fails the test: a skip would guard nothing
    return app


@pytest.fixture
def app_log_db(monkeypatch, tmp_path):
    app = _app()
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    app._init_people_table()
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        cx.execute("INSERT INTO people (email, tags) VALUES (?, ?)",
                   (BLOCKED, json.dumps(["type:client", "consent:unsubscribed"])))
        cx.commit()
    return app, path


@pytest.fixture
def inbox_db(monkeypatch, tmp_path):
    path = str(tmp_path / "chat_log.db")
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        _person(cx, BLOCKED, ["consent:unsubscribed"])
    monkeypatch.setattr(inbox, "_db_path", lambda: path)
    monkeypatch.setattr(inbox, "_is_undeliverable", lambda e: False)
    return path


def test_new_scan_email_skips_a_hub_unsubscribe(app_log_db, spy, monkeypatch):
    app, _ = app_log_db
    sent = []
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda to, subject, **kw: sent.append(to) or {"via": "ghl"})

    app._send_new_scan_email(BLOCKED, "2026-09-15", 9, "portal-token")
    assert (BLOCKED, True) in spy, "the suppression check was never reached"
    assert sent == []

    app._send_new_scan_email(CLEAN, "2026-09-15", 9, "portal-token")
    assert sent == [CLEAN]


def test_review_ready_mail_skips_a_hub_unsubscribe(spy, monkeypatch):
    from dashboard import supplement_review_notify as srn
    from dashboard import supplement_reviews as sr
    from dashboard import supplement_reviews_actions as sra
    cx = sqlite3.connect(":memory:")
    sr.init_table(cx)
    es.init_table(cx)
    _person(cx, BLOCKED, ["consent:unsubscribed"])
    sent = []
    monkeypatch.setattr(ghl_email, "is_configured", lambda: True)
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda to, subject, **kw: sent.append(to) or {"id": "x", "via": "ghl"})
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")

    def ready(email):
        rid = sr.create_request(cx, email, "Neuro Magnesium", "Acme", source="portal")["id"]
        sr.set_draft(cx, rid, "Review text.")
        return rid

    rid = ready(BLOCKED)
    out = sra._exec_confirm({"id": rid}, {"cx": cx, "actor": {}})
    assert out["notified"] == {"sent": False, "reason": "suppressed"}
    assert srn.notify_confirmed(cx, rid) == {"sent": False, "reason": "suppressed"}
    assert (BLOCKED, True) in spy
    assert sent == []

    out = sra._exec_confirm({"id": ready(CLEAN)}, {"cx": cx, "actor": {}})
    assert out["notified"]["sent"] is True and sent == [CLEAN]


def test_inbox_send_email_skips_a_hub_unsubscribe(inbox_db, spy, monkeypatch):
    built, executed = [], []
    monkeypatch.setattr(inbox, "_get_gmail_service", lambda: built.append(1) or object())
    monkeypatch.setattr(inbox, "_execute_send",
                        lambda svc, payload: executed.append(payload)
                        or {"id": "m1", "threadId": "t1"})
    # send_email returns before its suppression check under pytest. Lift that guard
    # for these two calls only, with both Gmail entry points already recorders.
    assert inbox._execute_send.__name__ == "<lambda>"
    assert inbox._get_gmail_service.__name__ == "<lambda>"
    marker = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        blocked = inbox.send_email(BLOCKED, "Subject", "Body")
        blocked_calls = (list(built), list(executed))
        clean = inbox.send_email(CLEAN, "Subject", "Body")
    finally:
        if marker is not None:
            os.environ["PYTEST_CURRENT_TEST"] = marker

    assert blocked == {"skipped": "suppressed"}
    assert (BLOCKED, True) in spy
    assert blocked_calls == ([], [])
    assert clean.get("id") == "m1" and len(executed) == 1


def test_inbox_send_bulk_skips_a_hub_unsubscribe(inbox_db, spy, monkeypatch):
    ghl_sent, gmail_sent = [], []
    monkeypatch.setenv("BULK_VIA_GHL", "1")
    monkeypatch.setattr(ghl_email, "is_configured", lambda: True)
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda to, subject, **kw: ghl_sent.append(to) or {"id": "g", "via": "ghl"})
    monkeypatch.setattr(inbox, "send_email",
                        lambda to, *a, **k: gmail_sent.append(to) or {"id": "m"})

    assert inbox.send_bulk(BLOCKED, "Subject", "Body") == {"skipped": "suppressed"}
    assert (BLOCKED, True) in spy
    assert ghl_sent == [] and gmail_sent == []

    inbox.send_bulk(CLEAN, "Subject", "Body")
    assert ghl_sent == [CLEAN] and gmail_sent == []


def test_sequence_runner_skips_a_hub_unsubscribe(tmp_path, spy, monkeypatch):
    from dashboard import sequences, unsubscribe
    from scripts import sequence_runner as runner
    monkeypatch.setattr(runner, "_UNDER_TEST", False)
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    sent = []
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda to, subject, **kw: sent.append(to) or {"id": "g", "via": "ghl"})

    cx = db.connect(str(tmp_path / "log.db"))
    sequences.init_tables(cx)
    es.init_table(cx)
    _person(cx, BLOCKED, ["consent:unsubscribed"])
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     steps=[{"step_no": 1, "subject": "one", "body_md": "Aloha",
                             "delay_days": 0}])
    sequences.set_active(cx, "nurture", True)
    for email in (BLOCKED, CLEAN):
        sequences.enroll(cx, "nurture", email, enrolled_at="2026-09-01T12:00:00Z")

    counts = runner.run_once(cx, now=datetime.datetime(2026, 9, 1, 12, 0, 0))

    assert (BLOCKED, True) in spy
    assert counts["suppressed"] == 1 and counts["sent"] == 1
    assert sent == [CLEAN]


def _full_report_transports(monkeypatch):
    calls = []
    monkeypatch.setattr(inbox, "send_email",
                        lambda to, *a, **k: calls.append(("gmail", to)) or {"id": "m"})
    monkeypatch.setattr(ghl_email, "is_configured", lambda: True)
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda to, *a, **k: calls.append(("ghl", to)) or {"via": "ghl"})
    import smtplib

    class _SMTP:
        def __init__(self, *a, **k):
            calls.append(("smtp", a))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    return calls


def test_full_report_skips_a_hub_unsubscribe(app_log_db, spy, monkeypatch):
    app, _ = app_log_db
    calls = _full_report_transports(monkeypatch)

    out = app._send_full_report_email(BLOCKED, "Name", "Subject", "Body",
                                      respect_suppression=True)
    assert out == ("suppressed", None)
    assert (BLOCKED, True) in spy
    assert calls == []

    out = app._send_full_report_email(CLEAN, "Name", "Subject", "Body",
                                      respect_suppression=True)
    assert out == ("gmail-api", None) and calls == [("gmail", CLEAN)]


def test_sign_in_links_are_still_exempt(app_log_db, spy, monkeypatch):
    """Spec H: a sign-in or recovery link passes respect_suppression=False by design."""
    app, _ = app_log_db
    calls = _full_report_transports(monkeypatch)
    out = app._send_full_report_email(BLOCKED, "Name", "Sign in", "Link",
                                      respect_suppression=False)
    assert out == ("gmail-api", None) and calls == [("gmail", BLOCKED)]
    assert spy == []
