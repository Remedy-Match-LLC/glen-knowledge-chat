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
