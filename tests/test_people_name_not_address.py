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
        assert app._repair_address_names(cx) == 2               # a's name, d's first name
        cx.commit()
        assert app._repair_address_names(cx) == 0               # idempotent
    assert _person(db, "a@x.com")["name"] == "Ann Lee"
    assert _person(db, "b@x.com")["name"] == "b@x.com"
    assert _person(db, "c@x.com")["name"] == "Cara Moe"
    assert (_person(db, "d@x.com")["name"], _person(db, "d@x.com")["first_name"]) == ("d@x.com", "")


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


# ── Review rounds 1 and 2 ────────────────────────────────────────────────────
def test_blank_incoming_name_never_replaces_a_stored_real_name(app_db):
    """Round 1 and 2: {'first_name': 'Pea'} alone turned "Peach Goddard" into "Pea"."""
    app, db = app_db
    _seed(db, "p@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "", "first_name": "Pea"})
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"


def test_a_shorter_incoming_name_never_replaces_a_fuller_one(app_db):
    """Round 1: GHL holding only "Peach" cut "Peach Goddard" to "Peach" every hour."""
    app, db = app_db
    _seed(db, "p@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "Peach", "first_name": "Peach"})
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"
    _upsert(app, db, {"email": "p@x.com", "name": "Peach Smith"})       # a real change still lands
    assert _person(db, "p@x.com")["name"] == "Peach Smith"


def test_an_address_never_lands_in_first_or_last_name(app_db):
    app, db = app_db
    _seed(db, "p@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "b@x.com", "first_name": "b@x.com",
                      "last_name": "c@x.com"})
    p = _person(db, "p@x.com")
    assert (p["name"], p["first_name"], p["last_name"]) == ("Peach Goddard", "Peach", "Goddard")


def test_an_address_inside_a_name_is_removed():
    from dashboard.name_case import strip_addresses
    assert strip_addresses("Peach d@x.com") == "Peach"
    assert strip_addresses("Peach Goddard <p@x.com>") == "Peach Goddard"
    assert strip_addresses("p@x.com") == ""
    assert strip_addresses("Ann @ Home Studio") == "Ann @ Home Studio"
    assert strip_addresses(None) == ""


def test_ghl_contact_name_drops_an_address_part():
    sys.path.insert(0, str(ROOT))
    import console_push_cron as cpc
    assert cpc._contact_name({"firstName": "b@x.com", "lastName": "Lee"}) == "Lee"
    assert cpc._contact_name({"firstName": "Peach", "lastName": "d@x.com"}) == "Peach"


def test_merge_rebuilds_from_the_keepers_own_names_first(app_db):
    """Round 1: a keeper with an address-as-name took the dupe's "P. Goddard" although its
    own first and last names said Peach Goddard."""
    app, db = app_db
    keep = _seed(db, "gmail@x.com", name="gmail@x.com", first="Peach", last="Goddard")
    dupe = _seed(db, "aol@x.com", name="P. Goddard", first="P.", last="Goddard")
    with sqlite3.connect(db) as cx:
        app._merge_two_people(cx, keep, dupe)
        cx.commit()
    assert _person(db, "gmail@x.com")["name"] == "Peach Goddard"


def test_console_editor_never_saves_an_address_as_the_name(app_db, monkeypatch):
    app, db = app_db
    monkeypatch.setattr(app, "CONSOLE_SECRET", "k")
    _seed(db, "p@x.com", name="Peach Goddard", first="Peach", last="Goddard")
    r = app.app.test_client().post("/api/people", headers={"X-Console-Key": "k"},
                                   json={"email": "p@x.com", "name": "p@x.com",
                                         "first_name": "Peach", "last_name": "Goddard"})
    assert r.status_code == 200
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"


# ── Review round 3 ───────────────────────────────────────────────────────────
def test_table_setup_does_not_repair_names(app_db):
    """Round 3: _init_people_table runs inside public routes, so the repair must not."""
    app, db = app_db
    _seed(db, "a@x.com", name="a@x.com", first="Ann", last="Lee")
    app._init_people_table()
    assert _person(db, "a@x.com")["name"] == "a@x.com"


def test_a_stored_name_mixed_with_an_address_is_cleaned(app_db):
    app, db = app_db
    _seed(db, "p@x.com", name="Peach p@x.com")
    _seed(db, "q@x.com", name="Q q@x.com", first="Quinn", last="Ray")
    with sqlite3.connect(db) as cx:
        assert app._repair_address_names(cx) == 2
        cx.commit()
    assert _person(db, "p@x.com")["name"] == "Peach"
    assert _person(db, "q@x.com")["name"] == "Quinn Ray"
    _seed(db, "r@x.com", name="Rae r@x.com", first="Rae", last="Lu")
    _upsert(app, db, {"email": "r@x.com", "name": "Rae"})
    assert _person(db, "r@x.com")["name"] == "Rae Lu"


def test_a_capitalisation_fix_still_lands(app_db):
    app, db = app_db
    _seed(db, "p@x.com", name="peach goddard")
    _upsert(app, db, {"email": "p@x.com", "name": "Peach Goddard"})
    assert _person(db, "p@x.com")["name"] == "Peach Goddard"


@pytest.mark.parametrize("raw,want", [
    ("Ann@Home Studio", "Ann@Home Studio"),      # no dotted domain: not an address
    ("J@ne Doe", "J@ne Doe"),
    ("Peach,d@x.com", "Peach"),
    ("Jane <j@x.com>.", "Jane"),
    ("Jr. Smith", "Jr. Smith"),
    ("mailto:a@b.com", ""),
])
def test_strip_addresses_edges(raw, want):
    from dashboard.name_case import strip_addresses
    assert strip_addresses(raw) == want


def test_a_rebuilt_name_is_capitalised(app_db):
    app, db = app_db
    _seed(db, "a@x.com", name="a@x.com", first="ann", last="lee")
    with sqlite3.connect(db) as cx:
        app._repair_address_names(cx)
        cx.commit()
    assert _person(db, "a@x.com")["name"] == "Ann Lee"


def test_repair_removes_an_address_from_first_and_last(app_db):
    """Production had 9 people with an address in first or last name; greetings read first."""
    app, db = app_db
    _seed(db, "f@x.com", name="Fay Lin", first="f@x.com", last="Lin")
    with sqlite3.connect(db) as cx:
        assert app._repair_address_names(cx) == 1
        cx.commit()
        assert app._repair_address_names(cx) == 0
    p = _person(db, "f@x.com")
    assert (p["name"], p["first_name"], p["last_name"]) == ("Fay Lin", "", "Lin")
