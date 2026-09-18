"""A client can delete the session they just recorded, without being identified.

Glen, 2026-09-18: 'a checkbox to "Delete this chat session" that is unchecked by default.
It would cover that particular session, one box for each session.'

WHY THIS DESIGN AND NOT A PER-PERSON DELETE. journal_blueprint records every entry with a
hardcoded user_id of "glen" (journal_blueprint.py:173), so nothing knows WHOSE transcript
any row is. A per-person path cannot find what it promises to delete. A per-SESSION box
can, because /journal/analyze already returns the row id to the browser: the client
deletes the record in front of them and nobody has to be identified.

THE ID ALONE IS NOT ENOUGH. It is a guessable integer, so an open route lets anyone delete
anyone's row. That fails in the safe direction -- vandalism, not disclosure -- but a signed
token costs nothing and stops a script clearing the table. The token is an HMAC over the id
and an expiry, so no new column is needed.

THE WHOLE ROW GOES. A journal row carries transcript_embedding, top_themes, emotion_scores,
polyvagal_state, lexical_metrics and congruence, all derived from what was said and several
near-reversible. Blanking the transcript and keeping the embedding would leave a
machine-readable trace of the same words while claiming the session was deleted.

THE WORDING NOW DESCRIBES WHAT EXISTS. Before this, the consent said a client could "ask
for it to be deleted at any time", which was a promise honoured by hand: there was no purge
job, no client route, and the only delete was staff-only.
"""
import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOORWAY = ROOT / "static" / "begin-doorway.html"


@pytest.fixture()
def jb(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    import importlib

    import journal_blueprint
    return importlib.reload(journal_blueprint)


# --- the token ---------------------------------------------------------------------

def test_a_token_verifies_for_its_own_entry(jb):
    tok = jb.delete_token(42)
    assert tok and jb._delete_token_ok(42, tok)


def test_a_token_does_not_work_for_another_entry(jb):
    """The whole point. Otherwise one token deletes the table."""
    assert not jb._delete_token_ok(43, jb.delete_token(42))


def test_a_forged_or_empty_token_is_refused(jb):
    for bad in ("", "nope", "1.2", "999999999.deadbeef"):
        assert not jb._delete_token_ok(42, bad), bad


def test_a_token_expires(jb):
    tok = jb.delete_token(42)
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert not jb._delete_token_ok(42, tok, now=later)


def test_no_secret_means_no_token_rather_than_an_unsigned_one(jb, monkeypatch):
    """An empty secret must not yield a token everyone can forge. The box then stays
    hidden, which is better than a delete that cannot be authorised."""
    monkeypatch.setattr(jb, "_DELETE_SECRET", "")
    assert jb.delete_token(42) == ""
    assert not jb._delete_token_ok(42, "anything")


# --- the store ---------------------------------------------------------------------

def test_delete_entry_removes_the_whole_row():
    from dashboard import journal_store
    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    with sqlite3.connect(db) as cx:
        journal_store.init_table(cx)
        journal_store.insert(cx, {"user_id": "glen", "recorded_at": "2026-09-18",
                                  "transcript": "something private",
                                  "transcript_embedding": [0.1, 0.2]})
        assert cx.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 1
        rid = cx.execute("SELECT id FROM journal_entries").fetchone()[0]
        assert journal_store.delete_entry(cx, rid) is True
        assert cx.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 0


def test_deleting_a_missing_row_reports_false_rather_than_raising():
    from dashboard import journal_store
    d = tempfile.mkdtemp()
    with sqlite3.connect(os.path.join(d, "t.db")) as cx:
        journal_store.init_table(cx)
        assert journal_store.delete_entry(cx, 999) is False


# --- the route ---------------------------------------------------------------------

def test_the_route_is_registered_and_post_only(jb):
    rules = [r for r in jb.journal_bp.deferred_functions]
    src = (ROOT / "journal_blueprint.py").read_text()
    assert '@journal_bp.route("/journal/entry/<int:entry_id>/delete", methods=["POST"])' in src


def test_the_route_checks_the_token_before_touching_the_database(jb):
    src = (ROOT / "journal_blueprint.py").read_text()
    body = src[src.index("def delete_entry_route"):]
    gate, write = body.index("_delete_token_ok"), body.index("delete_entry(cx")
    assert gate < write, "the row is deleted before the token is checked"


def test_the_analysis_response_carries_the_token(jb):
    """Unwired, the whole thing is inert."""
    src = (ROOT / "journal_blueprint.py").read_text()
    assert '"delete_token": delete_token(saved_id)' in src


# --- the page ----------------------------------------------------------------------

def test_the_box_exists_and_is_never_pre_ticked():
    src = DOORWAY.read_text()
    assert 'id="deleteChk"' in src
    assert "$('deleteChk').checked = false" in src, "it must be unchecked every time"
    assert "checked>" not in src.split('id="deleteChk"')[1][:60]


def test_the_box_only_appears_when_a_delete_is_possible():
    """Offering it without a saved row or a token is a button that cannot keep its word."""
    src = DOORWAY.read_text()
    assert "analysis.id && analysis.delete_token" in src


def test_the_page_no_longer_promises_a_hand_honoured_deletion():
    flat = " ".join(DOORWAY.read_text().split())
    assert "ask us to delete" not in flat
    assert "ask for it to be deleted" not in flat
    assert "delete this session myself with the box under my reflection" in flat
    assert "box under your reflection to delete the whole session" in flat


def test_the_consent_gate_from_1727_survived_the_merge():
    """This stacks on the consent PR. A resolution that dropped either handler would be
    a silent regression of a privacy fix."""
    src = DOORWAY.read_text()
    assert "voiceChk').addEventListener" in src
    assert "deleteChk').addEventListener" in src
    assert "<<<<<<<" not in src and ">>>>>>>" not in src
