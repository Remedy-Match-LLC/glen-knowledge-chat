"""A GHL contact merge moves the merged-away address into `additionalEmails`.

people found it on 2026-09-24. The sync sent only the primary `email`, so a bounce or
DND on a merged contact never reached its other addresses. 7 hub addresses kept
receiving mail after GHL had marked the contact bounced.

Glen, 2026-09-24: block all addresses. GHL's bounce tag belongs to the contact and
does not say which address bounced (additionalEmails entries carry only the address),
so every address on the contact gets the block the primary already gets.

  - The sync reads additionalEmails from the v2 search it already runs, and sends
    them as `additional_emails`. v1 does not return them.
  - The hub writes address-level rows for them and nothing else. It never creates a
    hub person from one: that would add a duplicate person for every merge.
  - A person-level refusal blocks the extra addresses as an opt-out.
  - A bounce tag already STORED in the hub writes nothing (people's decision: hub 587
    was left open on purpose after GHL cleared its bounce).
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

import console_push_cron as cron  # noqa: E402
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


def _row(path, email):
    with sqlite3.connect(path) as cx:
        try:
            return cx.execute("SELECT bounce_type, source FROM email_suppression WHERE email=?",
                              (email,)).fetchone()
        except sqlite3.OperationalError:
            return None


def _people(path):
    with sqlite3.connect(path) as cx:
        return {r[0] for r in cx.execute("SELECT email FROM people")}


def _suppressed(path, email):
    with sqlite3.connect(path) as cx:
        return es.is_suppressed(cx, email)


# ── the hub ──────────────────────────────────────────────────────────────────

def test_a_bounce_blocks_every_address_on_the_contact(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": ["old@x.com", "older@x.com"]})
    assert _row(path, "main@x.com") == ("hard", "ghl")
    assert _row(path, "old@x.com") == ("hard", "ghl")
    assert _row(path, "older@x.com") == ("hard", "ghl")
    assert _suppressed(path, "old@x.com") is True


def test_an_extra_address_never_becomes_a_hub_person(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": ["old@x.com"]})
    assert _people(path) == {"main@x.com"}


def test_an_extra_address_that_is_already_a_hub_person_is_blocked_not_changed(app_db):
    """The 7 merges each left a hub person on the old address. Block it; people owns
    merging the two."""
    app, path = app_db
    _seed(path, "old@x.com", ["type:client", "consent:opted-in"])
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": ["old@x.com"]})
    assert _row(path, "old@x.com") == ("hard", "ghl")
    with sqlite3.connect(path) as cx:
        tags = set(json.loads(cx.execute("SELECT tags FROM people WHERE email='old@x.com'")
                              .fetchone()[0]))
    assert tags == {"type:client", "consent:opted-in"}


def test_an_existing_row_on_an_extra_address_is_never_rewritten(app_db):
    app, path = app_db
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        es.add(cx, "old@x.com", "hard", "merged-away bounce", "people-merge-gap")
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": ["old@x.com"]})
    assert _row(path, "old@x.com") == ("hard", "people-merge-gap")


def test_an_address_level_email_dnd_blocks_every_address(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "email_dnd": "active",
                        "email_dnd_message": "Updated by contact merge",
                        "additional_emails": ["old@x.com"]})
    assert _row(path, "main@x.com") == ("ghl-dnd", "ghl")
    assert _row(path, "old@x.com") == ("ghl-dnd", "ghl")


@pytest.mark.parametrize("person", [
    {"dnd": True},
    {"tags": ["do not email"]},
    {"email_dnd": "active", "email_dnd_message": "I asked to stop"},
], ids=["v1-dnd", "refusal-tag", "refusal-dnd"])
def test_a_refusal_blocks_the_extra_addresses_as_an_opt_out(app_db, person):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "additional_emails": ["old@x.com"], **person})
    assert _row(path, "old@x.com")[0] in ("optout", "ghl-dnd")
    assert _suppressed(path, "old@x.com") is True


def test_a_v1_dnd_refusal_writes_an_opt_out_row(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "dnd": True, "additional_emails": ["old@x.com"]})
    assert _row(path, "old@x.com") == ("optout", "ghl")
    assert _row(path, "main@x.com") is None, "the primary's refusal stays the consent tag"


def test_no_signal_writes_nothing_for_the_extra_addresses(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "tags": ["type:client"],
                        "additional_emails": ["old@x.com"]})
    assert _row(path, "old@x.com") is None
    assert _suppressed(path, "old@x.com") is False


def test_extra_addresses_are_normalised_and_junk_is_ignored(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": ["  Old@X.com ", "", None, "not-an-address",
                                              {"email": "dict@x.com"}, "MAIN@x.com"]})
    with sqlite3.connect(path) as cx:
        rows = {r[0] for r in cx.execute("SELECT email FROM email_suppression")}
    assert rows == {"main@x.com", "old@x.com"}


def test_a_non_list_additional_emails_is_ignored(app_db):
    app, path = app_db
    _upsert(app, path, {"email": "main@x.com", "tags": ["email bounced"],
                        "additional_emails": "old@x.com"})
    assert _row(path, "old@x.com") is None


def test_a_stored_bounce_tag_alone_writes_nothing(app_db):
    """people, 2026-09-24: hub 587's bounce was cleared in GHL and it is engaging. Its
    hub tag stays as history and must not re-block it on the next sync."""
    app, path = app_db
    _seed(path, "cleared@x.com", ["email bounced", "consent:opted-in"])
    _upsert(app, path, {"email": "cleared@x.com", "tags": ["consent:opted-in"],
                        "additional_emails": ["other@x.com"]})
    assert _row(path, "cleared@x.com") is None
    assert _row(path, "other@x.com") is None


def test_the_merge_endpoint_carries_additional_emails_through(app_db):
    app, path = app_db
    r = app.app.test_client().post(
        "/api/people?merge_tags=1", headers={"X-Console-Key": "testkey"},
        json=[{"email": "main@x.com", "tags": ["email bounced"],
               "additional_emails": ["old@x.com"]}])
    assert r.status_code == 200
    assert _row(path, "old@x.com") == ("hard", "ghl")
    assert _people(path) == {"main@x.com"}


# ── the sync ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


V1_CONTACTS = [
    {"id": "c1", "email": "Main@Example.com", "tags": ["email bounced"], "dnd": False},
    {"id": "c2", "email": "two@example.com", "tags": [], "dnd": False},
]


@pytest.fixture
def ghl(monkeypatch):
    state = {"v2": [], "posted": [], "secrets": {"GHL_PIT": "pit-fake"}}
    monkeypatch.setattr(cron, "GHL_API_KEY", "v1-fake")
    monkeypatch.setattr(cron, "_get_secret", lambda name: state["secrets"].get(name, ""))
    monkeypatch.setattr(cron.time, "sleep", lambda s: None)

    def fake_get(url, headers=None, params=None, timeout=None):
        return _Resp(200, {"contacts": V1_CONTACTS if params["page"] == 1 else []})

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/contacts/search"):
            nxt = state["v2"].pop(0) if state["v2"] else (200, [])
            if isinstance(nxt, Exception):
                raise nxt
            status, contacts = nxt
            return _Resp(status, {"contacts": contacts})
        state["posted"].extend(json)
        return _Resp(200, {"inserted": len(json), "updated": 0})

    monkeypatch.setattr(cron.requests, "get", fake_get)
    monkeypatch.setattr(cron.requests, "post", fake_post)
    return state


def _by_id(posted):
    return {p["ghl_id"]: p for p in posted}


def test_the_sync_sends_a_contacts_additional_emails(ghl):
    """The live v2 shape, read 2026-09-24: a list of {"email": ...} objects."""
    ghl["v2"] = [(200, [
        {"id": "c1", "dndSettings": {}, "searchAfter": [1, "c1"],
         "additionalEmails": [{"email": "Old@Example.com "}, {"email": "main@example.com"},
                              {"email": ""}, {}, "bare@example.com",
                              {"email": "not-an-address"}]},
        {"id": "c2", "dndSettings": {}, "searchAfter": [2, "c2"], "additionalEmails": []},
    ])]
    cron.sync_people_from_ghl()
    people = _by_id(ghl["posted"])
    assert people["c1"]["additional_emails"] == ["bare@example.com", "old@example.com"]
    assert "additional_emails" not in people["c2"], "no extras means no new field"


def test_extras_still_arrive_with_no_email_dnd(ghl):
    ghl["v2"] = [(200, [{"id": "c1", "additionalEmails": [{"email": "old@example.com"}]}])]
    cron.sync_people_from_ghl()
    c1 = _by_id(ghl["posted"])["c1"]
    assert c1["additional_emails"] == ["old@example.com"]
    assert "email_dnd" not in c1


@pytest.mark.parametrize("failure", [(500, []), RuntimeError("cloudflare 1010")])
def test_a_failed_v2_read_sends_no_additional_emails(ghl, failure):
    ghl["v2"] = [failure]
    cron.sync_people_from_ghl()
    assert ghl["posted"], "the v1 sync still ran"
    assert all("additional_emails" not in p for p in ghl["posted"])
