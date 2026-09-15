"""GHL email signals in the People hub upsert, per people-48's field spec (2026-09-15).

  - `consent:unsubscribed` means only "the person asked to stop email", and is permanent.
  - A GHL "email bounced" tag blocks the ADDRESS (email_suppression, hard, source ghl)
    and changes nothing about the person's consent. The raw tag stays as history.
  - An active v2 email DND is a refusal unless its message is a known non-refusal
    writer (people-48's ruling, 2026-09-15): the email service or a contact merge or
    the Z-015-4 bounce workflow block the address only; a Twilio carrier error and
    the Z-016 re-subscribe workflows write nothing. Unknown or blank means refusal.
  - The v1 all-channel `dnd` flag is a refusal.
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

Z015_4 = "Updated from workflow_2d7fa93f-e008-461d-b2c4-711c4acf3f2d"
Z016_01 = "Updated from workflow_49556753-45ee-45d2-a6b1-c576f6c26b88"
Z016_02 = "Updated from workflow_6d0f6dc3-fbe5-48c8-aa0b-b6db3ce92878"


@pytest.mark.parametrize("message", [EMAIL_SERVICE, "Updated by contact merge", Z015_4],
                         ids=["a-email-service", "b-contact-merge", "d-z015-4-bounced"])
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


def _assert_refusal(app, path, email, message):
    _seed(path, email, ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": email, "email_dnd": "active", "email_dnd_message": message})
    t = _tags(path, email)
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t
    assert _suppressed(path, email) is True


def test_free_text_email_dnd_is_a_refusal(app_db):
    app, path = app_db
    _assert_refusal(app, path, "ft@x.com", "I no longer want to receive these emails")


def test_an_unknown_workflow_email_dnd_is_a_refusal(app_db):
    """Includes the Z-015 unsubscribe and spam workflows, which are not on the list."""
    app, path = app_db
    _assert_refusal(app, path, "wf@x.com",
                    "Updated from workflow_00000000-1111-2222-3333-444444444444")


def test_a_blank_email_dnd_message_is_a_refusal(app_db):
    app, path = app_db
    _assert_refusal(app, path, "blank@x.com", "")


def test_a_twilio_carrier_error_writes_nothing_for_email(app_db):
    app, path = app_db
    _seed(path, "tw@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "tw@x.com", "email_dnd": "active",
                        "email_dnd_message": "TWILIO_ERROR_CODE: 30006"})
    assert _row(path, "tw@x.com") is None
    t = _tags(path, "tw@x.com")
    assert "consent:unsubscribed" not in t and "consent:opted-in" in t
    assert _suppressed(path, "tw@x.com") is False


@pytest.mark.parametrize("message", [Z016_01, Z016_02], ids=["z016-01", "z016-02"])
@pytest.mark.parametrize("status", ["active", "inactive"])
def test_a_resubscribe_workflow_never_blocks(app_db, message, status):
    app, path = app_db
    _seed(path, "rs@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "rs@x.com", "email_dnd": status, "email_dnd_message": message})
    assert _row(path, "rs@x.com") is None
    t = _tags(path, "rs@x.com")
    assert "consent:unsubscribed" not in t and "consent:opted-in" in t


def test_an_inactive_dnd_from_an_unknown_writer_never_blocks(app_db):
    app, path = app_db
    _seed(path, "in@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "in@x.com", "email_dnd": "inactive",
                        "email_dnd_message": "I no longer want to receive these emails"})
    assert _row(path, "in@x.com") is None
    assert "consent:unsubscribed" not in _tags(path, "in@x.com")


def test_the_v1_all_channel_dnd_flag_is_a_refusal(app_db):
    """people-48, 2026-09-15: v1 dnd keeps mapping to consent:unsubscribed."""
    app, path = app_db
    _seed(path, "v1@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "v1@x.com", "dnd": True})
    t = _tags(path, "v1@x.com")
    assert "consent:unsubscribed" in t and "consent:opted-in" not in t


def test_a_resubscribe_writer_never_downgrades_an_opt_out(app_db):
    app, path = app_db
    _seed(path, "stay@x.com", ["consent:unsubscribed"])
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        es.add(cx, "stay@x.com", "hard", "NXDOMAIN", "bounce-scan")
    _upsert(app, path, {"email": "stay@x.com", "email_dnd": "inactive",
                        "email_dnd_message": Z016_01, "tags": ["consent:opted-in"]})
    assert "consent:unsubscribed" in _tags(path, "stay@x.com")
    assert _row(path, "stay@x.com") == ("hard", "bounce-scan")


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
