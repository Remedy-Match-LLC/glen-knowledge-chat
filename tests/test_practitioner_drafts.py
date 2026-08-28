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
