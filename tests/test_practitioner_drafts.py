"""Unit tests for the practitioner profile draft store.

Uses sqlite as a stand-in for the Postgres cursor: every statement in this
module is portable, which is itself the constraint being tested.
"""
import json
import sqlite3

import pytest

from dashboard import practitioner_drafts as pd

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def cur(tmp_path):
    """Named `cur` throughout for brevity, but it is a sqlite CONNECTION --
    the same thing dashboard/referrals.py and ff_match_drafts.py take."""
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.row_factory = sqlite3.Row
    pd.init_tables(cx)
    return cx


def test_statuses_are_the_three_lifecycle_states():
    assert pd.STATUSES == frozenset({"draft", "submitted", "approved"})


def test_get_draft_returns_none_when_absent(cur):
    assert pd.get_draft(cur, PID) is None


def test_upsert_creates_a_draft_status_row(cur):
    out = pd.upsert_draft(cur, PID, {"bio": "hello"})
    assert out["status"] == "draft"
    assert out["fields"] == {"bio": "hello"}


def test_upsert_is_idempotent_on_practitioner_id(cur):
    pd.upsert_draft(cur, PID, {"bio": "one"})
    pd.upsert_draft(cur, PID, {"bio": "two"})
    assert pd.get_draft(cur, PID)["fields"] == {"bio": "two"}


def test_editing_an_approved_draft_returns_it_to_draft(cur):
    """A published practitioner who edits again must re-enter review."""
    pd.upsert_draft(cur, PID, {"bio": "one"})
    cur.execute("UPDATE practitioner_profile_drafts SET status='approved'"
                " WHERE practitioner_id=?", (PID,))
    pd.upsert_draft(cur, PID, {"bio": "two"})
    assert pd.get_draft(cur, PID)["status"] == "draft"


def test_upsert_survives_a_stale_read_of_its_own_row(cur, monkeypatch):
    """I5: check-then-act. The settings POST does not hold _db_lock, and
    _db_lock is a threading.Lock() anyway -- process-local, worthless across
    Render instances. A double-clicked Save is two writers whose reads both
    said "no row yet"; the loser used to run a bare INSERT and raise
    IntegrityError, 500ing the practitioner.

    Simulated by making every read return None while the row already exists.
    One ON CONFLICT statement absorbs that; a read-then-INSERT-or-UPDATE
    raises. The row is then verified with raw SQL, not through get_draft.
    """
    pd.upsert_draft(cur, PID, {"bio": "first"})
    monkeypatch.setattr(pd, "get_draft", lambda cx, pid: None)

    pd.upsert_draft(cur, PID, {"bio": "second"})  # must not raise

    rows = cur.execute("SELECT fields FROM practitioner_profile_drafts"
                       " WHERE practitioner_id=?", (PID,)).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["fields"]) == {"bio": "second"}


def test_upsert_preserves_created_at_across_an_edit(cur):
    """created_at is deliberately absent from the DO UPDATE list."""
    pd.upsert_draft(cur, PID, {"bio": "one"})
    created = pd.get_draft(cur, PID)["created_at"]
    pd.upsert_draft(cur, PID, {"bio": "two"})
    assert pd.get_draft(cur, PID)["created_at"] == created


PID2 = "22222222-2222-2222-2222-222222222222"


def test_submit_moves_draft_to_submitted(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    assert pd.submit(cur, PID) is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "submitted" and d["submitted_at"]


def test_submit_is_false_when_there_is_no_draft(cur):
    assert pd.submit(cur, PID) is False


def test_approve_marks_approved_and_stamps_review_time(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    assert pd.approve(cur, PID) is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "approved" and d["reviewed_at"]


def test_approve_refuses_a_row_that_was_never_submitted(cur):
    """Approving straight from 'draft' would skip the practitioner's own submit."""
    pd.upsert_draft(cur, PID, {"bio": "x"})
    assert pd.approve(cur, PID) is False
    assert pd.get_draft(cur, PID)["status"] == "draft"


def test_reject_returns_it_to_draft_with_the_note(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    assert pd.reject(cur, PID, "please remove the health claim") is True
    d = pd.get_draft(cur, PID)
    assert d["status"] == "draft"
    assert d["review_note"] == "please remove the health claim"


def test_reject_requires_a_note(cur):
    pd.upsert_draft(cur, PID, {"bio": "x"})
    pd.submit(cur, PID)
    with pytest.raises(ValueError):
        pd.reject(cur, PID, "")


def test_list_by_status_returns_only_that_status(cur):
    pd.upsert_draft(cur, PID, {"bio": "a"})
    pd.upsert_draft(cur, PID2, {"bio": "b"})
    pd.submit(cur, PID)
    subs = pd.list_by_status(cur, "submitted")
    assert [d["practitioner_id"] for d in subs] == [PID]


def test_beta_policy_reviews_every_known_field():
    """Conservative on purpose. Relaxing a field must be a one-line policy
    change here, never a schema change."""
    assert all(v == "review" for v in pd.REVIEW_POLICY.values())


def test_unknown_fields_default_to_review():
    assert pd.needs_review("a_field_invented_next_year") is True


def test_needs_review_reads_the_policy():
    pd.REVIEW_POLICY["city"] = "auto"
    try:
        assert pd.needs_review("city") is False
        assert pd.needs_review("bio") is True
    finally:
        pd.REVIEW_POLICY["city"] = "review"


def test_split_by_policy_separates_auto_from_review():
    pd.REVIEW_POLICY["city"] = "auto"
    try:
        auto, review = pd.split_by_policy({"city": "Hilo", "bio": "x"})
        assert auto == {"city": "Hilo"}
        assert review == {"bio": "x"}
    finally:
        pd.REVIEW_POLICY["city"] = "review"


def test_split_by_policy_sends_everything_to_review_under_beta_policy():
    auto, review = pd.split_by_policy({"bio": "x", "city": "Hilo", "state": "HI"})
    assert auto == {}
    assert review == {"bio": "x", "city": "Hilo", "state": "HI"}


from dashboard import practitioner_profile as pp


def test_publish_draft_refuses_an_unapproved_draft(cur, monkeypatch):
    """The gate: only an APPROVED draft may reach the public table."""
    pd.upsert_draft(cur, PID, {"bio": "x"})
    written = {}
    monkeypatch.setattr(pp, "_write_live_profile", lambda pid, f: written.update(f))
    assert pp.publish_draft(cur, PID) is False
    assert written == {}, "an unapproved draft must not reach the live table"


def test_publish_draft_writes_live_only_when_approved(cur, monkeypatch):
    pd.upsert_draft(cur, PID, {"bio": "hello"})
    pd.submit(cur, PID)
    pd.approve(cur, PID)
    written = {}
    monkeypatch.setattr(pp, "_write_live_profile", lambda pid, f: written.update(f))
    assert pp.publish_draft(cur, PID) is True
    assert written["bio"] == "hello"


def test_save_draft_never_touches_the_live_table(cur, monkeypatch):
    """The whole point of section 2a: saving is not publishing."""
    called = {"n": 0}
    monkeypatch.setattr(pp, "_write_live_profile",
                        lambda pid, f: called.__setitem__("n", called["n"] + 1))
    pp.save_draft(cur, PID, {"bio": "hello", "city": "Hilo", "state": "HI"})
    assert called["n"] == 0
    assert pd.get_draft(cur, PID)["fields"]["bio"] == "hello"
