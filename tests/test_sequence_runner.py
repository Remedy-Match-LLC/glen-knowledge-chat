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


def _sends(cx, monkeypatch, runner, outcome="ses"):
    sent = []
    monkeypatch.setattr(runner, "send_one",
                        lambda c, **kw: (sent.append(kw), (outcome, "msg-1"))[1])
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


class _FakeSES:
    def __init__(self):
        self.calls = []

    def send_email(self, **kw):
        self.calls.append(kw)
        return {"MessageId": "ses-123"}


@pytest.fixture()
def fake_ses(monkeypatch):
    """The real send_one through the real ses_mail, with AWS replaced by a fake."""
    from dashboard import ses_mail
    fake = _FakeSES()
    monkeypatch.setenv("SES_SEND_ENABLED", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "test-config-set")
    monkeypatch.setattr(ses_mail, "_client", lambda: pytest.fail("real AWS client"))
    real_send = ses_mail.send
    # pytest re-sets PYTEST_CURRENT_TEST for the call phase, so the fake client is passed
    # in: send's own guard lets a send with an explicit client through.
    monkeypatch.setattr(ses_mail, "send", lambda *a, **k: real_send(*a, **{**k, "client": fake}))
    return fake


def test_the_send_goes_through_ses_never_ghl(cx, monkeypatch, runner, fake_ses):
    from dashboard import ghl_email
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda *a, **k: pytest.fail("GoHighLevel is retired"))
    counts = runner.run_once(cx, now=NOW)
    assert counts["sent"] == 1 and len(fake_ses.calls) == 1
    assert fake_ses.calls[0]["Destination"] == {"ToAddresses": ["a@b.com"]}
    row = cx.execute("SELECT status, message_id FROM sequence_sends "
                     "WHERE email='a@b.com' AND step_no=1").fetchone()
    assert row[0] == "sent" and row[1] == "ses-123"


def test_the_ses_mail_carries_an_unsubscribe_scoped_to_the_sequence(cx, runner, fake_ses):
    runner.run_once(cx, now=NOW)
    raw = fake_ses.calls[0]["Content"]["Raw"]["Data"].decode("utf-8", "replace")
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in raw
    assert unsubscribe.sign("a@b.com", "nurture") in raw.replace("=\r\n", "").replace("=\n", "")
    # The header, the text footer and the html footer: one footer per part, never two.
    assert raw.count("/email/unsubscribe?") == 3
    assert "Dr. Glen Swartwout" in raw


def test_a_send_ses_surely_did_not_make_frees_the_claim(cx, monkeypatch, runner):
    monkeypatch.delenv("SES_SEND_ENABLED", raising=False)      # SES switched off
    _sends(cx, monkeypatch, runner, outcome=None)
    counts = runner.run_once(cx, now=NOW)
    assert counts["not_sent"] == 1 and counts["failed"] == 1 and counts["sent"] == 0
    assert cx.execute("SELECT COUNT(*) FROM sequence_sends").fetchone()[0] == 0
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    assert len(sent) == 1                              # the next tick tries again


def test_a_send_ses_may_have_made_is_never_retried(cx, monkeypatch, runner):
    _sends(cx, monkeypatch, runner, outcome="unknown")
    counts = runner.run_once(cx, now=NOW)
    assert counts["unknown"] == 1
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    assert sent == []
    row = cx.execute("SELECT status FROM sequence_sends WHERE step_no=1").fetchone()
    assert row[0] == "unknown" and counts["failed"] == 1


def test_a_refusal_while_ses_is_on_fails_once_and_is_not_retried(cx, monkeypatch, runner):
    monkeypatch.setenv("SES_SEND_ENABLED", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "test-config-set")
    calls = _sends(cx, monkeypatch, runner, outcome=None)
    later = NOW + datetime.timedelta(minutes=15)
    c1 = runner.run_once(cx, now=NOW)
    runner.run_once(cx, now=later)
    assert c1["failed"] == 1 and c1["not_sent"] == 0 and len(calls) == 1
    row = cx.execute("SELECT status, error FROM sequence_sends WHERE step_no=1").fetchone()
    assert row[0] == "failed" and "refused" in row[1]


def test_a_malformed_address_is_refused_before_sending(cx, monkeypatch, runner):
    sequences.enroll(cx, "nurture", "x@y.com\nbcc: z@q.com", enrolled_at=_iso(NOW))
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    assert all("\n" not in s["to_email"] for s in sent)
    rows = cx.execute("SELECT status, error FROM sequence_sends "
                      "WHERE status='failed'").fetchall()
    assert rows == [("failed", "malformed address")]


@pytest.mark.parametrize("text,first,want", [
    ("Aloha {{first_name}},\n\nBody", "Mona", "Aloha Mona,\n\nBody"),
    ("Aloha {{first_name}},\n\nBody", "", "Aloha,\n\nBody"),
    ("Hi {{ contact.first_name }}! See you, {{first_name}}.", "", "Hi! See you,."),
    ("No field here", "", "No field here"),
])
def test_personalise_fills_or_drops_the_first_name(runner, text, first, want):
    assert runner.personalise(text, first) == want


def test_the_sent_email_never_shows_the_merge_field(cx, monkeypatch, runner):
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     steps=[{"step_no": 1, "subject": "For {{first_name}}",
                             "body_md": "Aloha {{first_name}},\n\nBody", "delay_days": 0}])
    cx.execute("CREATE TABLE IF NOT EXISTS people (email TEXT, name TEXT)")
    cx.execute("INSERT INTO people (email, name) VALUES ('a@b.com', 'mona smith')")
    cx.commit()
    sent = _sends(cx, monkeypatch, runner)
    runner.run_once(cx, now=NOW)
    assert "{{" not in sent[0]["body_md"] and "{{" not in sent[0]["subject"]
    assert sent[0]["body_md"].startswith("Aloha Mona,")


def test_a_dry_run_prints_no_address(cx, monkeypatch, runner, capsys):
    runner.run_once(cx, now=NOW, dry_run=True)
    assert "a@b.com" not in capsys.readouterr().out


def test_a_claim_left_by_a_crash_is_closed_never_resent(cx, monkeypatch, runner):
    """A crash after SES accepted the email and before mark_sent leaves a claim. A later
    tick closes it as skipped, so the email is never sent twice (review round 2)."""
    sequences.claim(cx, "nurture", 1, "a@b.com",
                    claimed_at=_iso(NOW - datetime.timedelta(hours=2)))
    sent = _sends(cx, monkeypatch, runner)
    counts = runner.run_once(cx, now=NOW)
    assert counts["abandoned"] == 1 and sent == []
    row = cx.execute("SELECT status, error FROM sequence_sends WHERE step_no=1").fetchone()
    assert row[0] == "skipped" and "may have gone out" in row[1]


def test_an_ses_suppression_is_recorded_as_skipped(cx, monkeypatch, runner):
    _sends(cx, monkeypatch, runner, outcome="suppressed")
    counts = runner.run_once(cx, now=NOW)
    assert counts["suppressed"] == 1
    row = cx.execute("SELECT status FROM sequence_sends WHERE step_no=1").fetchone()
    assert row[0] == "skipped"


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
    def boom(c, **kw):
        raise RuntimeError("transport exploded")
    monkeypatch.setattr(runner, "send_one", boom)
    counts = runner.run_once(cx, now=NOW)
    assert counts["failed"] == 1
    row = cx.execute("SELECT status, error FROM sequence_sends "
                     "WHERE email='a@b.com'").fetchone()
    assert row[0] == "failed" and row[1] == "RuntimeError"


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
    assert sr.send_one(None, to_email="a@b.com", subject="s", body_md="x",
                       slug="nurture") == (None, None)
