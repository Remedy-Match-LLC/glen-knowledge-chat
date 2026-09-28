"""A person's name must never be an email address.

2026-09-27: the hourly GoHighLevel sync set `name` to the email whenever the contact had no
first or last name there, and the additive upsert let that overwrite a real name every hour.
Peach Goddard's Gmail record showed her address twice in the merge tool. 638 people carried
an address as their name; 73 of them held a first or last name it could be rebuilt from."""
import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _app():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
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
    app._init_people_table()
    return app, db


def _seed(db, email, name="", first="", last=""):
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO people (email, name, first_name, last_name, tags, created_at,"
                   " updated_at) VALUES (?,?,?,?,'[]','','')", (email, name, first, last))
        cx.commit()
        return cx.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()[0]


def _person(db, email):
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return dict(cx.execute("SELECT * FROM people WHERE email=?", (email,)).fetchone())


def _upsert(app, db, person):
    with sqlite3.connect(db) as cx:
        app._upsert_person_additive(cx, person)
        cx.commit()


def test_is_address():
    from dashboard.name_case import is_address
    assert is_address("peachgoddard@gmail.com")
    assert is_address("  A@B.co ")
    assert not is_address("Peach Goddard")
    assert not is_address("")
    assert not is_address(None)
    assert not is_address("Ann @ Home Studio")      # a name with an @ in it, not an address


def test_ghl_contact_with_no_name_sends_no_name():
    sys.path.insert(0, str(ROOT))
    import console_push_cron as cpc
    assert cpc._contact_name({"firstName": "", "lastName": ""}) == ""
    assert cpc._contact_name({"firstName": " Peach ", "lastName": "Goddard"}) == "Peach Goddard"
    assert cpc._contact_name({}) == ""


def test_an_incoming_address_never_replaces_a_real_name(app_db):
    app, db = app_db
    _seed(db, "p@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "p@x.com"})
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"


def test_a_stored_address_name_is_rebuilt_from_first_and_last(app_db):
    """Peach's Gmail record: name = the address, first and last hold her name."""
    app, db = app_db
    _seed(db, "p@x.com", name="p@x.com", first="Peach", last="Goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "p@x.com", "first_name": "", "last_name": ""})
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"


def test_a_new_person_with_no_name_is_not_named_by_address(app_db):
    app, db = app_db
    _upsert(app, db, {"email": "new@x.com", "name": "new@x.com"})
    assert _person(db, "new@x.com")["name"] == ""
    _upsert(app, db, {"email": "two@x.com", "name": "", "first_name": "Ann", "last_name": "Lee"})
    assert _person(db, "two@x.com")["name"] == "Ann Lee"


def test_merge_takes_the_real_name_over_an_address(app_db):
    app, db = app_db
    keep = _seed(db, "gmail@x.com", name="gmail@x.com")
    dupe = _seed(db, "aol@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    with sqlite3.connect(db) as cx:
        app._merge_two_people(cx, keep, dupe)
        cx.commit()
    p = _person(db, "gmail@x.com")
    assert (p["name"], p["first_name"], p["last_name"]) == ("Peach Goddard", "Peach", "Goddard")


def test_merge_keeps_the_survivors_real_name(app_db):
    app, db = app_db
    keep = _seed(db, "gmail@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    dupe = _seed(db, "aol@x.com", name="P. Goddard", first="P.", last="Goddard")
    with sqlite3.connect(db) as cx:
        app._merge_two_people(cx, keep, dupe)
        cx.commit()
    p = _person(db, "gmail@x.com")
    assert (p["name"], p["first_name"]) == ("Peach Goddard", "Peach")


def test_repair_rebuilds_only_names_it_can(app_db):
    app, db = app_db
    _seed(db, "a@x.com", name="a@x.com", first="Ann", last="Lee")
    _seed(db, "b@x.com", name="b@x.com")                       # nothing to rebuild from
    _seed(db, "c@x.com", name="Cara Moe", first="Cara", last="Moe")
    _seed(db, "d@x.com", name="d@x.com", first="d@x.com")       # first name is an address too
    with sqlite3.connect(db) as cx:
        assert app._repair_address_names(cx) == 1
        cx.commit()
        assert app._repair_address_names(cx) == 0               # idempotent
    assert _person(db, "a@x.com")["name"] == "Ann Lee"
    assert _person(db, "b@x.com")["name"] == "b@x.com"
    assert _person(db, "c@x.com")["name"] == "Cara Moe"
    assert _person(db, "d@x.com")["name"] == "d@x.com"


def test_merge_takes_a_real_name_even_without_first_and_last(app_db):
    app, db = app_db
    keep = _seed(db, "gmail@x.com", name="gmail@x.com")
    dupe = _seed(db, "aol@x.com", name="Peach Goddard")
    with sqlite3.connect(db) as cx:
        app._merge_two_people(cx, keep, dupe)
        cx.commit()
    assert _person(db, "gmail@x.com")["name"] == "Peach Goddard"


def test_repair_leaves_a_real_name_with_an_at_sign(app_db):
    app, db = app_db
    _seed(db, "e@x.com", name="Ann @ Home Studio", first="Ann", last="Lee")
    with sqlite3.connect(db) as cx:
        assert app._repair_address_names(cx) == 0
    assert _person(db, "e@x.com")["name"] == "Ann @ Home Studio"
