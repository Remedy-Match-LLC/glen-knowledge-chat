"""The runner: claim, check, send, record.

Still dark in slice 3 — every sequence ships inactive. These tests cover the
order of operations, because the order is the safety property: claim before
send, and check suppression at SEND time rather than only at enrollment.
"""
import datetime
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import db, email_suppression as es, sequences, unsubscribe  # noqa: E402

STEPS = [
    {"step_no": 1, "subject": "one", "body_md": "Aloha,\n\nBody one.", "delay_days": 0},
    {"step_no": 2, "subject": "two", "body_md": "Body two", "delay_days": 4},
]
NOW = datetime.datetime(2026, 9, 1, 12, 0, 0)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def runner(monkeypatch):
    from scripts import sequence_runner as sr
    importlib.reload(sr)
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    # The runner must not be silenced by the transport's pytest guard alone.
    monkeypatch.setattr(sr, "_UNDER_TEST", False, raising=False)
    return sr


@pytest.fixture()
def cx(tmp_path):
    c = db.connect(str(tmp_path / "log.db"))
    sequences.init_tables(c)
    es.init_table(c)
    sequences.upsert(c, slug="nurture", name="Nurture", trigger_kind="manual",
                     steps=STEPS)
    sequences.set_active(c, "nurture", True)
    sequences.enroll(c, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    return c


def _sends(cx, monkeypatch, runner):
    sent = []
    monkeypatch.setattr(runner, "send_one",
                        lambda **kw: (sent.append(kw), "msg-1")[1])
    return sent


def test_a_due_step_is_sent_and_recorded(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    counts = runner.run_once(cx, now=NOW)
    assert counts["sent"] == 1
    assert sent[0]["to_email"] == "a@b.com"
    assert sent[0]["subject"] == "one"
    row = cx.execute("SELECT status, message_id FROM sequence_sends "
                     "WHERE email='a@b.com' AND step_no=1").fetchone()
    assert row[0] == "sent" and row[1] == "msg-1"


def test_the_body_carries_an_unsubscribe_link_scoped_to_the_sequence(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    assert "/email/unsubscribe?" in sent[0]["html"]
    assert unsubscribe.sign("a@b.com", "nurture") in sent[0]["html"]


def test_a_suppressed_address_is_skipped_at_send_time(cx, monkeypatch, runner):
    """Suppression is checked when sending, not only when enrolling. Someone can
    opt out on day 2 of a 25-day drip."""
    sent = _sends(cx, monkeypatch, runner)
    es.add_optout(cx, "a@b.com", "unsubscribe-link:nurture")
    counts = runner.run_once(cx, now=NOW)
    assert sent == []
    assert counts["suppressed"] == 1
    row = cx.execute("SELECT status FROM sequence_sends WHERE email='a@b.com'").fetchone()
    assert row[0] == "skipped"


def test_a_send_failure_is_recorded_and_not_retried_blindly(cx, monkeypatch, runner):
    def boom(**kw):
        raise RuntimeError("transport exploded")
    monkeypatch.setattr(runner, "send_one", boom)
    counts = runner.run_once(cx, now=NOW)
    assert counts["failed"] == 1
    row = cx.execute("SELECT status, error FROM sequence_sends "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "failed" and "exploded" in row[1]


def test_dry_run_sends_nothing_and_claims_nothing(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    counts = runner.run_once(cx, now=NOW, dry_run=True)
    assert sent == []
    assert counts["would_send"] == 1
    assert cx.execute("SELECT COUNT(*) FROM sequence_sends").fetchone()[0] == 0, \
        "a dry run must leave no claim behind"


def test_a_second_tick_sends_nothing_more(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    runner.run_once(cx, now=NOW)
    assert len(sent) == 1


def test_stale_steps_are_skipped_with_a_reason(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    sequences.enroll(cx, "nurture", "old@b.com",
                     enrolled_at=_iso(NOW - datetime.timedelta(days=40)))
    counts = runner.run_once(cx, now=NOW, max_catchup_days=2)
    assert [s["to_email"] for s in sent] == ["a@b.com"]
    assert counts["skipped_stale"] == 2
    row = cx.execute("SELECT status, error FROM sequence_sends "
                     "WHERE email='old@b.com' AND step_no=1").fetchone()
    assert row[0] == "skipped" and "overdue" in row[1].lower()


def test_an_inactive_sequence_sends_nothing(cx, monkeypatch, runner):
    sent = _sends(cx, monkeypatch, runner)
    sequences.set_active(cx, "nurture", False)
    counts = runner.run_once(cx, now=NOW)
    assert sent == [] and counts["sent"] == 0


def test_the_runner_refuses_to_send_under_pytest_by_default():
    """The transport has its own guard, but a bare full-suite run has sent real
    email before. The runner carries its own, at its own entry point."""
    from scripts import sequence_runner as sr
    importlib.reload(sr)
    assert sr._UNDER_TEST is True
    assert sr.send_one(to_email="a@b.com", subject="s", html="<p>x</p>") is None
