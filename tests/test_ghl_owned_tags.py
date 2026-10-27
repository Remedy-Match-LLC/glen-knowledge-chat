"""GHL-owned tags leave the hub when GHL drops them (Glen, 2026-10-27, platform tab).

The hourly GHL sync unions tags, so a tag GHL removes stays in the hub forever. GHL
keeps one "email engage" window per contact and moves it as the last engagement ages;
the hub kept every window ever set (people-b3, 2026-10-27: 4,981 of 7,678 stale tags).

Only the GHL sync passes ghl_owned=1, and only the tags in _GHL_OWNED_TAGS can drop.
Every other tag stays additive: consent, type, source, bounce and other feeders' tags.
"""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

W0, W8, W15, W22 = ("email engage 0-7 days", "email engage 8-14 days",
                    "email engage 15-21 days", "email engage 22-28 days")


def _app():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
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


def _seed(db, email, tags, ghl_id=""):
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO people (email, tags, ghl_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (email, json.dumps(tags), ghl_id, "", ""))
        cx.commit()


def _tags(db, email):
    with sqlite3.connect(db) as cx:
        return set(json.loads(cx.execute("SELECT tags FROM people WHERE email=?", (email,)).fetchone()[0]))


def _upsert(app, db, person, **kw):
    with sqlite3.connect(db) as cx:
        res = app._upsert_person_additive(cx, person, **kw)
        cx.commit()
    return res


def test_the_list_is_exactly_the_four_email_engage_windows(app_db):
    app, _ = app_db
    assert app._GHL_OWNED_TAGS == frozenset({W0, W8, W15, W22})


def test_a_window_ghl_dropped_leaves_and_the_current_one_stays(app_db):
    app, db = app_db
    _seed(db, "a@x.com", [W0, W8, W22, "type:client"])
    _upsert(app, db, {"email": "a@x.com", "ghl_id": "g1", "tags": [W15]}, ghl_owned=True)
    assert _tags(db, "a@x.com") == {W15, "type:client"}


def test_without_the_flag_every_tag_stays(app_db):
    """Every other feeder posts merge_tags=1 alone, and its partial tag list must
    never remove a window."""
    app, db = app_db
    _seed(db, "b@x.com", [W0, W8])
    _upsert(app, db, {"email": "b@x.com", "tags": ["source:practitioner-finder"]})
    assert _tags(db, "b@x.com") == {W0, W8, "source:practitioner-finder"}


def test_tags_off_the_list_never_drop(app_db):
    app, db = app_db
    keep = ["consent:unsubscribed", "consent:opted-in", "type:prospect", "source:ghl",
            "email bounced", "client", "e4l account", "state:has-scanned",
            "email engage 29-35 days", "email engage"]
    _seed(db, "c@x.com", keep + [W0])
    _upsert(app, db, {"email": "c@x.com", "ghl_id": "g1", "tags": []}, ghl_owned=True)
    t = _tags(db, "c@x.com")
    assert W0 not in t
    # consent:opted-in loses to unsubscribed by the existing precedence rule, not this one
    assert t == set(keep) - {"consent:opted-in"}


def test_a_payload_with_no_tags_field_drops_nothing(app_db):
    """No tags field means GHL did not say; an empty list means GHL holds none."""
    app, db = app_db
    _seed(db, "d@x.com", [W0])
    _upsert(app, db, {"email": "d@x.com", "ghl_id": "g1", "phone": "555"}, ghl_owned=True)
    assert _tags(db, "d@x.com") == {W0}


def test_a_tags_field_that_is_not_a_list_drops_nothing(app_db):
    app, db = app_db
    _seed(db, "d2@x.com", [W0])
    _upsert(app, db, {"email": "d2@x.com", "ghl_id": "g1", "tags": None}, ghl_owned=True)
    assert _tags(db, "d2@x.com") == {W0}


def test_case_and_spacing_do_not_matter(app_db):
    app, db = app_db
    _seed(db, "e@x.com", ["Email Engage 0-7 Days", "email  engage 15-21 days",
                          "email  engage 8-14 days"])
    _upsert(app, db, {"email": "e@x.com", "ghl_id": "g1", "tags": ["EMAIL ENGAGE 8-14 DAYS"]}, ghl_owned=True)
    t = {x.lower() for x in _tags(db, "e@x.com")}
    assert W0 not in t
    # The double-spaced stale window is dropped too, spelled as it was stored.
    assert "email  engage 15-21 days" not in t
    assert not any(" ".join(x.split()) == W15 for x in t)
    assert any(" ".join(x.split()) == W8 for x in t)


def test_a_refusal_in_the_same_payload_still_drops_stale_windows(app_db):
    """The DND branch rebuilds the tags column from the stored set; it must use the
    pruned set, or an unsubscribe would put every old window back."""
    app, db = app_db
    _seed(db, "f@x.com", [W0, W8, "consent:opted-in"])
    _upsert(app, db, {"email": "f@x.com", "ghl_id": "g1", "tags": [W22], "dnd": True}, ghl_owned=True)
    assert _tags(db, "f@x.com") == {W22, "consent:unsubscribed"}


def test_a_merged_survivor_keeps_its_windows(app_db):
    """A survivor of a person merge can have two GHL contacts, each with its own
    window. Dropping per contact would flip the tag every hour, so it stays additive."""
    app, db = app_db
    from dashboard import person_aliases as pal
    _seed(db, "g@x.com", [W0, W8])
    with sqlite3.connect(db) as cx:
        pal.init_tables(cx)
        pal.add_alias(cx, "old-g@x.com", "g@x.com", 1)
        cx.commit()
    _upsert(app, db, {"email": "g@x.com", "ghl_id": "g1", "tags": [W15]}, ghl_owned=True)
    assert _tags(db, "g@x.com") == {W0, W8, W15}


def test_an_alias_address_drops_nothing_on_the_survivor(app_db):
    app, db = app_db
    from dashboard import person_aliases as pal
    _seed(db, "h@x.com", [W0])
    with sqlite3.connect(db) as cx:
        pal.init_tables(cx)
        pal.add_alias(cx, "old-h@x.com", "h@x.com", 1)
        cx.commit()
    _upsert(app, db, {"email": "old-h@x.com", "ghl_id": "g1", "tags": [W8]}, ghl_owned=True)
    assert _tags(db, "h@x.com") == {W0, W8}


def test_a_payload_with_no_ghl_contact_id_drops_nothing(app_db):
    """Only the GHL sync sends a contact id. Another caller with the flag and the
    console key still cannot clear a window without one."""
    app, db = app_db
    _seed(db, "i@x.com", [W0])
    _upsert(app, db, {"email": "i@x.com", "tags": []}, ghl_owned=True)
    _upsert(app, db, {"email": "i@x.com", "ghl_id": "  ", "tags": []}, ghl_owned=True)
    assert _tags(db, "i@x.com") == {W0}


def test_the_stored_contact_id_matches_and_windows_drop(app_db):
    app, db = app_db
    _seed(db, "j@x.com", [W0], ghl_id="g1")
    _upsert(app, db, {"email": "j@x.com", "ghl_id": "g1", "tags": [W8]}, ghl_owned=True)
    assert _tags(db, "j@x.com") == {W8}


def test_two_ghl_contacts_for_one_address_never_flip_the_window(app_db):
    """Two GHL contacts share the address and sync one after the other. In this order
    each differs from the id stored by the other, so neither drops a window. In another
    order (the stored id matching the contact that syncs first) the first drops the
    other's window and the second adds it back in the same run: the end state is both
    contacts' windows either way, and only consent-free window tags are ever touched."""
    app, db = app_db
    _seed(db, "k@x.com", [W0], ghl_id="gA")
    for _ in range(2):
        _upsert(app, db, {"email": "k@x.com", "ghl_id": "gB", "tags": [W8]}, ghl_owned=True)
        _upsert(app, db, {"email": "k@x.com", "ghl_id": "gA", "tags": [W15]}, ghl_owned=True)
    assert _tags(db, "k@x.com") == {W0, W8, W15}


def test_a_new_person_gets_only_the_incoming_tags(app_db):
    app, db = app_db
    assert _upsert(app, db, {"email": "n@x.com", "ghl_id": "g1", "tags": [W0]}, ghl_owned=True) == "inserted"
    assert _tags(db, "n@x.com") == {W0}


def _post(app, url, body):
    return app.app.test_client().post(url, json=body, headers={"X-Console-Key": "testkey"})


def test_the_route_drops_only_with_ghl_owned(app_db):
    app, db = app_db
    _seed(db, "r@x.com", [W0])
    _seed(db, "s@x.com", [W0])
    assert _post(app, "/api/people?merge_tags=1", [{"email": "r@x.com", "ghl_id": "g1", "tags": [W8]}]).status_code == 200
    assert _post(app, "/api/people?merge_tags=1&ghl_owned=1",
                 [{"email": "s@x.com", "ghl_id": "g1", "tags": [W8]}]).status_code == 200
    assert _tags(db, "r@x.com") == {W0, W8}
    assert _tags(db, "s@x.com") == {W8}


def test_ghl_owned_without_merge_tags_is_still_additive(app_db):
    """ghl_owned only narrows the additive path. Alone it must not fall into the
    console editor's overwrite path, which replaces every tag."""
    app, db = app_db
    _seed(db, "t@x.com", [W0, "type:client"])
    assert _post(app, "/api/people?ghl_owned=1",
                 [{"email": "t@x.com", "ghl_id": "g1", "tags": [W8]}]).status_code == 200
    assert _tags(db, "t@x.com") == {W8, "type:client"}


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_the_ghl_sync_sends_the_flag(monkeypatch):
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import console_push_cron as c
    urls = []
    monkeypatch.setattr(c, "GHL_API_KEY", "fake-ghl")
    monkeypatch.setattr(c, "fetch_email_dnd_v2", lambda: None)
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: _Resp(200, {"contacts": [
        {"id": "c1", "email": "a@example.invalid", "tags": [W0]}]}))
    monkeypatch.setattr(c.requests, "post",
                        lambda url, *a, **k: urls.append(url) or _Resp(200, {"updated": 1}))
    assert c.sync_people_from_ghl() == 0
    assert len(urls) == 1
    assert "merge_tags=1" in urls[0] and "ghl_owned=1" in urls[0]
