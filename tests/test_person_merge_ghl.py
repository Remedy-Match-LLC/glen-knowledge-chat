"""The old address's GoHighLevel contact after a merge: tagged, and optionally do-not-contact.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import json
import sqlite3

import pytest

AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


@pytest.fixture
def app_(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "GHL_API_KEY", "fake")
    return appmod


def _ghl(monkeypatch, appmod, contacts):
    puts = []
    monkeypatch.setattr(appmod, "_ghl_get", lambda path, params=None: ({"contacts": contacts}, None))
    monkeypatch.setattr(appmod, "_ghl_put", lambda path, payload: puts.append((path, payload)) or ({}, None))
    return puts


def test_stop_sets_dnd_and_the_tag(app_, monkeypatch):
    puts = _ghl(monkeypatch, app_, [{"id": "c1", "tags": ["a"], "dateAdded": "1"}])
    cid, err = app_.ghl_mark_merged(AOL, 2, stop=True)
    assert (cid, err) == ("c1", None)
    assert puts == [("/contacts/c1", {"tags": ["a", "merged-into-2"], "dnd": True})]


def test_keep_sets_only_the_tag(app_, monkeypatch):
    puts = _ghl(monkeypatch, app_, [{"id": "c1", "tags": [], "dateAdded": "1"}])
    app_.ghl_mark_merged(AOL, 2, stop=False)
    assert puts == [("/contacts/c1", {"tags": ["merged-into-2"]})]


def test_no_contact_is_a_no_op(app_, monkeypatch):
    puts = _ghl(monkeypatch, app_, [])
    assert app_.ghl_mark_merged(AOL, 2, stop=True) == (None, None)
    assert puts == []


def test_unmark_without_the_tag_changes_nothing(app_, monkeypatch):
    puts = _ghl(monkeypatch, app_, [{"id": "c1", "tags": ["a"], "dateAdded": "1"}])
    app_.ghl_unmark_merged(AOL, 2)
    assert puts == []


def test_the_marked_contact_never_reaches_the_survivors_consent(app_):
    """The sync reads a do-not-contact it does not recognise as a refusal. From a merged
    address it must carry tags only."""
    app_._init_people_table()
    from dashboard import person_aliases as pa
    cx = sqlite3.connect(app_.LOG_DB)
    cx.execute("INSERT INTO people (id, email, name, tags, created_at, updated_at) "
               "VALUES (2, ?, 'Mel', '[]', 't', 't')", (GMAIL,))
    pa.init_tables(cx)
    pa.add_alias(cx, AOL, GMAIL, 1)
    cx.commit()
    cx.row_factory = sqlite3.Row
    before = dict(cx.execute("SELECT * FROM people WHERE id=2").fetchone())
    app_._upsert_person_additive(cx, {"email": AOL, "tags": ["merged-into-2"], "dnd": True,
                                      "email_dnd": "active", "email_dnd_message": "anything"})
    cx.commit()
    after = dict(cx.execute("SELECT * FROM people WHERE id=2").fetchone())
    assert after == before
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "email_suppression" in tables:
        assert cx.execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0] == 0


def test_undo_never_clears_do_not_contact(app_, monkeypatch):
    """The client may have asked for do-not-contact since the merge (review round 2)."""
    puts = _ghl(monkeypatch, app_, [{"id": "c1", "tags": ["a", "merged-into-2"], "dateAdded": "1"}])
    app_.ghl_unmark_merged(AOL, 2)
    assert puts == [("/contacts/c1", {"tags": ["a"]})]
