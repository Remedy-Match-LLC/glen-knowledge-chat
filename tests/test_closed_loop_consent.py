"""Closed-loop consent: a GHL DND/unsubscribe (carried via console_push) revokes
consent:opted-in in the People hub through _upsert_person_additive."""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest


def _app():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Without these the whole file SKIPS in the secretless CI, and a green skip
    # guards nothing. The clients construct fine with dummy keys; nothing here
    # calls them.
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    app = _app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    app._init_people_table()
    return app, db


def _seed(db, email, tags):
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES (?,?,?,?)",
                   (email, json.dumps(tags), "", ""))
        cx.commit()


def _tags(db, email):
    with sqlite3.connect(db) as cx:
        return set(json.loads(cx.execute("SELECT tags FROM people WHERE email=?", (email,)).fetchone()[0]))


def test_dnd_flag_revokes_optin(app_db):
    app, db = app_db
    _seed(db, "u@x.com", ["type:client", "consent:opted-in"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {"email": "u@x.com", "dnd": True})
        cx.commit()
    t = _tags(db, "u@x.com")
    assert "consent:opted-in" not in t and "consent:unsubscribed" in t
    assert "type:client" in t  # other tags preserved


def test_email_bounced_tag_revokes(app_db):
    app, db = app_db
    _seed(db, "b@x.com", ["type:client", "consent:opted-in"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {"email": "b@x.com", "tags": ["email bounced"]})
        cx.commit()
    t = _tags(db, "b@x.com")
    assert "consent:opted-in" not in t and "consent:unsubscribed" in t


def test_sms_unsubscribe_does_not_revoke_email(app_db):
    app, db = app_db
    _seed(db, "s@x.com", ["type:client", "consent:opted-in"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {"email": "s@x.com", "tags": ["unsubscribed on sms"]})
        cx.commit()
    t = _tags(db, "s@x.com")
    assert "consent:opted-in" in t and "consent:unsubscribed" not in t


def test_normal_sync_does_not_revoke(app_db):
    app, db = app_db
    _seed(db, "ok@x.com", ["type:client", "consent:opted-in"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {"email": "ok@x.com", "dnd": False, "tags": ["nes client"]})
        cx.commit()
    t = _tags(db, "ok@x.com")
    assert "consent:opted-in" in t and "consent:unsubscribed" not in t


def test_new_contact_with_dnd_inserts_unsubscribed(app_db):
    app, db = app_db
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {"email": "new@x.com", "tags": ["consent:opted-in"], "dnd": True})
        cx.commit()
    t = _tags(db, "new@x.com")
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t


def test_via_merge_endpoint(app_db):
    app, db = app_db
    _seed(db, "e@x.com", ["type:client", "consent:opted-in"])
    c = app.app.test_client()
    r = c.post("/api/people?merge_tags=1", json=[{"email": "e@x.com", "dnd": True}],
               headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    t = _tags(db, "e@x.com")
    assert "consent:opted-in" not in t and "consent:unsubscribed" in t


# ── a corrected row must survive the feeder that mislabelled it ───────────────
#
# com.remedymatch.practitioner-finder is loaded and runs on a schedule, and it
# stamps consent:cold-no-consent on anyone it considers unengaged.
# sync-media-contacts.py hardcodes the same tag on every row it posts. So the
# 363-person correction is only worth making if the next feeder run cannot undo it.
#
# _collapse_consent is what makes it stick. These prove it, because a comment
# saying so is not a demonstration.

def test_a_feeder_cannot_put_the_cold_tag_back(app_db):
    """The durability test. This is the whole reason the correction is safe."""
    app, db = app_db
    _seed(db, "fixed@x.com", ["consent:opted-in", "e4l account"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {
            "email": "fixed@x.com",
            "tags": ["consent:cold-no-consent", "type:practitioner-cold",
                     "source:practitioner-finder"]})
        cx.commit()
    t = _tags(db, "fixed@x.com")
    assert "consent:opted-in" in t
    assert "consent:cold-no-consent" not in t, "the feeder undid the correction"


def test_the_feeder_still_lands_its_other_tags(app_db):
    """The control. If the upsert silently dropped everything, the test above
    would pass for the wrong reason."""
    app, db = app_db
    _seed(db, "fixed2@x.com", ["consent:opted-in"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {
            "email": "fixed2@x.com",
            "tags": ["consent:cold-no-consent", "source:practitioner-finder"]})
        cx.commit()
    assert "source:practitioner-finder" in _tags(db, "fixed2@x.com")


def test_an_uncorrected_cold_row_keeps_its_cold_tag(app_db):
    """The 6,905 strangers are correctly cold and must stay that way."""
    app, db = app_db
    _seed(db, "stranger@x.com", ["consent:cold-no-consent"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {
            "email": "stranger@x.com", "tags": ["source:practitioner-finder"]})
        cx.commit()
    assert "consent:cold-no-consent" in _tags(db, "stranger@x.com")


def test_an_opt_out_still_outranks_the_correction(app_db):
    """Nothing in this work may put mail back in front of someone who left."""
    app, db = app_db
    _seed(db, "gone@x.com", ["consent:unsubscribed"])
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, {
            "email": "gone@x.com", "tags": ["consent:opted-in", "e4l account"]})
        cx.commit()
    t = _tags(db, "gone@x.com")
    assert "consent:unsubscribed" in t
    assert "consent:opted-in" not in t, "an opt-out was overridden"
