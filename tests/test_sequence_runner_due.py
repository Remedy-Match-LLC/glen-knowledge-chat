"""Which step is due, for whom, and when.

Slice 3. Still sends nothing: this is the arithmetic and the claim ledger.

The property that matters most is that a backdated enrollment cannot flood.
Slice 5 migrates ~40 people mid-flight by backdating `enrolled_at`, so on the
first tick several of their steps are already "due" by date. Sending those would
deliver four emails at once to a real client.
"""
import datetime

import pytest

from dashboard import db, sequences

STEPS = [
    {"step_no": 1, "subject": "one", "body_md": "b1", "delay_days": 0},
    {"step_no": 2, "subject": "two", "body_md": "b2", "delay_days": 4},
    {"step_no": 3, "subject": "three", "body_md": "b3", "delay_days": 10},
]


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def cx():
    c = db.connect(":memory:")
    sequences.init_tables(c)
    sequences.upsert(c, slug="nurture", name="Nurture", trigger_kind="manual",
                     steps=STEPS)
    sequences.set_active(c, "nurture", True)
    return c


NOW = datetime.datetime(2026, 9, 1, 12, 0, 0)


def test_step_one_is_due_immediately_on_enrollment(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    due = sequences.due(cx, now=NOW)
    assert [(d["email"], d["step_no"]) for d in due] == [("a@b.com", 1)]


def test_nothing_is_due_before_the_offset_elapses(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.mark_sent(cx, "nurture", 1, "a@b.com", "msg1")
    # day 2 of 4
    assert sequences.due(cx, now=NOW + datetime.timedelta(days=2)) == []


def test_the_next_step_becomes_due_at_its_offset(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.mark_sent(cx, "nurture", 1, "a@b.com", "msg1")
    due = sequences.due(cx, now=NOW + datetime.timedelta(days=4))
    assert [d["step_no"] for d in due] == [2]


def test_only_one_step_per_contact_per_tick(cx):
    # Enrolled 30 days ago with nothing sent: every step is past due by date.
    sequences.enroll(cx, "nurture", "a@b.com",
                     enrolled_at=_iso(NOW - datetime.timedelta(days=30)))
    due = sequences.due(cx, now=NOW, max_catchup_days=99)
    assert len(due) == 1, "a backdated enrollment must not release a burst"
    assert due[0]["step_no"] == 1


def test_a_long_overdue_step_is_skipped_not_sent(cx):
    """The flood guard. A step that came due weeks ago is stale: sending it now
    delivers a 'day 4' email on day 30. Mark it skipped and move on."""
    sequences.enroll(cx, "nurture", "a@b.com",
                     enrolled_at=_iso(NOW - datetime.timedelta(days=30)))
    due = sequences.due(cx, now=NOW, max_catchup_days=2)
    assert due == []
    stale = sequences.stale_steps(cx, now=NOW, max_catchup_days=2)
    assert [s["step_no"] for s in stale] == [1, 2, 3]


def test_a_recently_due_step_is_still_sent(cx):
    # Within the catch-up window: a cron that missed a tick must still deliver.
    sequences.enroll(cx, "nurture", "a@b.com",
                     enrolled_at=_iso(NOW - datetime.timedelta(hours=6)))
    due = sequences.due(cx, now=NOW, max_catchup_days=2)
    assert [d["step_no"] for d in due] == [1]


def test_an_inactive_sequence_yields_nothing(cx):
    sequences.set_active(cx, "nurture", False)
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    assert sequences.due(cx, now=NOW) == []


def test_an_unsubscribed_enrollment_yields_nothing(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.set_enrollment_status(cx, "nurture", "a@b.com", "unsubscribed")
    assert sequences.due(cx, now=NOW) == []


def test_a_finished_enrollment_yields_nothing(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    for n, day in ((1, 0), (2, 4), (3, 10)):
        sequences.mark_sent(cx, "nurture", n, "a@b.com", f"m{n}")
    assert sequences.due(cx, now=NOW + datetime.timedelta(days=60)) == []


def test_enroll_is_idempotent(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.enroll(cx, "nurture", "A@B.com",
                     enrolled_at=_iso(NOW + datetime.timedelta(days=5)))
    rows = cx.execute("SELECT enrolled_at FROM sequence_enrollments "
                      "WHERE slug='nurture'").fetchall()
    assert len(rows) == 1, "re-enrolling must not restart or duplicate someone"
    assert rows[0][0] == _iso(NOW), "the original enrollment date must win"


def test_claim_is_won_once(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    assert sequences.claim(cx, "nurture", 1, "a@b.com") is True
    assert sequences.claim(cx, "nurture", 1, "a@b.com") is False


def test_a_claimed_step_is_no_longer_due(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.claim(cx, "nurture", 1, "a@b.com")
    assert sequences.due(cx, now=NOW) == []


def test_a_stale_claim_is_released(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.claim(cx, "nurture", 1, "a@b.com",
                    claimed_at=_iso(NOW - datetime.timedelta(hours=3)))
    assert sequences.due(cx, now=NOW) == [], "still claimed"
    freed = sequences.release_stale_claims(cx, now=NOW, older_than_minutes=60)
    assert freed == 1
    assert [d["step_no"] for d in sequences.due(cx, now=NOW)] == [1]


def test_a_fresh_claim_is_not_released(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.claim(cx, "nurture", 1, "a@b.com", claimed_at=_iso(NOW))
    assert sequences.release_stale_claims(cx, now=NOW, older_than_minutes=60) == 0


def test_a_sent_step_is_never_released(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    sequences.claim(cx, "nurture", 1, "a@b.com",
                    claimed_at=_iso(NOW - datetime.timedelta(hours=9)))
    sequences.mark_sent(cx, "nurture", 1, "a@b.com", "msg1")
    assert sequences.release_stale_claims(cx, now=NOW, older_than_minutes=60) == 0
    assert sequences.due(cx, now=NOW) == []


def test_due_carries_what_the_sender_needs(cx):
    sequences.enroll(cx, "nurture", "a@b.com", enrolled_at=_iso(NOW))
    d = sequences.due(cx, now=NOW)[0]
    assert d["slug"] == "nurture"
    assert d["subject"] == "one"
    assert d["body_md"] == "b1"
    assert d["email"] == "a@b.com"
