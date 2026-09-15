"""GHL email signals in the People hub upsert, per people-48's field spec (2026-09-15).

  - `consent:unsubscribed` means only "the person asked to stop email", and is permanent.
  - A GHL "email bounced" tag blocks the ADDRESS (email_suppression, hard, source ghl)
    and changes nothing about the person's consent. The raw tag stays as history.
  - An active v2 email DND alone is address-level (bounce_type ghl-dnd). It becomes a
    refusal only alongside a refusal signal: an unsubscribe or spam tag, or a DND
    enabled "by customer".
  - Nothing removes an existing consent:unsubscribed.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import email_suppression as es  # noqa: E402

EMAIL_SERVICE = "Received Permanent Bounce/Spam/Unsubscribe from Email Service"


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    import app
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    app._init_people_table()
    return app, path


def _seed(path, email, tags):
    with sqlite3.connect(path) as cx:
        cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES (?,?,?,?)",
                   (email, json.dumps(tags), "", ""))
        cx.commit()


def _upsert(app, path, person):
    with sqlite3.connect(path) as cx:
        res = app._upsert_person_additive(cx, person)
        cx.commit()
    assert res in ("inserted", "updated")


def _tags(path, email):
    with sqlite3.connect(path) as cx:
        return set(json.loads(cx.execute("SELECT tags FROM people WHERE email=?",
                                          (email,)).fetchone()[0]))


def _row(path, email):
    with sqlite3.connect(path) as cx:
        try:
            return cx.execute("SELECT bounce_type, source FROM email_suppression WHERE email=?",
                              (email,)).fetchone()
        except sqlite3.OperationalError:
            return None


def _suppressed(path, email):
    with sqlite3.connect(path) as cx:
        return es.is_suppressed(cx, email)


# ── bounces are address-level ────────────────────────────────────────────────

def test_a_bounce_tag_blocks_the_address_and_leaves_consent_alone(app_db):
    app, path = app_db
    _seed(path, "b@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "b@x.com", "tags": ["email bounced"]})
    t = _tags(path, "b@x.com")
    assert _row(path, "b@x.com") == ("hard", "ghl")
    assert "consent:unsubscribed" not in t
    assert "consent:opted-in" in t, "a bounce is not a refusal"
    assert "email bounced" in t, "the raw tag stays as history"
    assert _suppressed(path, "b@x.com") is True


def test_a_new_contact_with_a_bounce_tag_gets_a_row_and_no_consent_tag(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "newb@x.com", "tags": ["Email Bounced"]})
    assert _row(path, "newb@x.com") == ("hard", "ghl")
    assert "consent:unsubscribed" not in _tags(path, "newb@x.com")


def test_an_existing_suppression_row_is_never_rewritten(app_db):
    app, path = app_db
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        es.add_optout(cx, "o@x.com", "unsubscribe-link:global")
    _upsert(app, path, {"email": "o@x.com", "tags": ["email bounced"],
                        "email_dnd": "active", "email_dnd_message": EMAIL_SERVICE})
    assert _row(path, "o@x.com") == ("optout", "unsubscribe-link:global")


# ── refusals are consent ─────────────────────────────────────────────────────

@pytest.mark.parametrize("tag", ["email unsubscribed", "do not email", "spam complaint"])
def test_a_refusal_tag_is_consent_unsubscribed(app_db, tag):
    app, path = app_db
    _seed(path, "u@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "u@x.com", "tags": [tag]})
    t = _tags(path, "u@x.com")
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t


def test_the_tag_the_upsert_writes_is_the_tag_the_check_reads(app_db):
    """A round trip through the real storage, so the check cannot be matching a
    format the hub never writes."""
    app, path = app_db
    _upsert(app, path, {"email": "rt@x.com", "tags": ["email unsubscribed"]})
    assert _row(path, "rt@x.com") is None
    assert _suppressed(path, "rt@x.com") is True


# ── v2 email DND ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", [EMAIL_SERVICE, "Updated by contact merge"])
def test_an_email_dnd_alone_is_address_level(app_db, message):
    app, path = app_db
    _seed(path, "d@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "d@x.com", "email_dnd": "active",
                        "email_dnd_message": message})
    t = _tags(path, "d@x.com")
    assert _row(path, "d@x.com") == ("ghl-dnd", "ghl")
    assert "consent:unsubscribed" not in t and "consent:opted-in" in t
    assert _suppressed(path, "d@x.com") is True


def test_an_email_dnd_plus_a_spam_tag_is_a_refusal(app_db):
    app, path = app_db
    _seed(path, "s@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "s@x.com", "tags": ["spam complaint"],
                        "email_dnd": "active", "email_dnd_message": EMAIL_SERVICE})
    t = _tags(path, "s@x.com")
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t
    assert _row(path, "s@x.com") == ("ghl-dnd", "ghl")


def test_an_email_dnd_enabled_by_customer_is_a_refusal(app_db):
    app, path = app_db
    _seed(path, "c@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "c@x.com", "email_dnd": "active",
                        "email_dnd_message": "DnD enabled by customer"})
    assert "consent:unsubscribed" in _tags(path, "c@x.com")


def test_an_inactive_email_dnd_changes_nothing(app_db):
    app, path = app_db
    _seed(path, "i@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "i@x.com", "email_dnd": "inactive",
                        "email_dnd_message": "DnD enabled by customer"})
    assert _row(path, "i@x.com") is None
    t = _tags(path, "i@x.com")
    assert "consent:unsubscribed" not in t and "consent:opted-in" in t


def test_the_merge_endpoint_carries_email_dnd_through(app_db):
    app, path = app_db
    _seed(path, "e@x.com", ["type:client", "consent:opted-in"])
    r = app.app.test_client().post(
        "/api/people?merge_tags=1", headers={"X-Console-Key": "testkey"},
        json=[{"email": "e@x.com", "email_dnd": "active", "email_dnd_message": EMAIL_SERVICE}])
    assert r.status_code == 200
    assert _row(path, "e@x.com") == ("ghl-dnd", "ghl")
    assert "consent:unsubscribed" not in _tags(path, "e@x.com")


# ── an existing opt-out is permanent ─────────────────────────────────────────

def test_an_existing_consent_unsubscribed_is_never_removed(app_db):
    """The ~883 bounce-derived rows stay blocked until Glen decides otherwise."""
    app, path = app_db
    _seed(path, "gone@x.com", ["consent:unsubscribed", "email bounced"])
    _upsert(app, path, {"email": "gone@x.com", "tags": ["email bounced"], "dnd": False,
                        "email_dnd": "active", "email_dnd_message": "Updated by contact merge"})
    _upsert(app, path, {"email": "gone@x.com", "tags": ["consent:opted-in", "e4l account"]})
    t = _tags(path, "gone@x.com")
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t
    assert _suppressed(path, "gone@x.com") is True
