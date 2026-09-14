"""Every writer of a person's name applies dashboard.name_case.

A one-off cleanup does not hold on its own: the additive upsert replaces a stored
name with any non-blank incoming one, so the next feeder run would put
"hannah van horn" back. Glen, 2026-09-13: fix it where names get saved.
"""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def _app():
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable in this env: {e}")


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    app = _app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    app._init_people_table()
    return app, db


def _names(db, email):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT name, first_name, last_name FROM people WHERE email=?",
                          (email,)).fetchone()


LOWER = {"email": "hvh@x.com", "name": "hannah van horn",
         "first_name": "hannah", "last_name": "van horn"}
FIXED = ("Hannah van Horn", "Hannah", "van Horn")


def test_a_feeder_insert_is_capitalised(app_db):
    app, db = app_db
    r = app.app.test_client().post("/api/people?merge_tags=1", json=[LOWER],
                                   headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    assert _names(db, "hvh@x.com") == FIXED


def test_a_feeder_resending_lowercase_does_not_undo_the_fix(app_db):
    app, db = app_db
    client, hdr = app.app.test_client(), {"X-Console-Key": "testkey"}
    client.post("/api/people?merge_tags=1", json=[dict(LOWER, name="Hannah van Horn",
                first_name="Hannah", last_name="van Horn")], headers=hdr)
    assert _names(db, "hvh@x.com") == FIXED          # reached: the stored row exists
    client.post("/api/people?merge_tags=1", json=[LOWER], headers=hdr)
    assert _names(db, "hvh@x.com") == FIXED


def test_the_overwrite_post_is_capitalised(app_db):
    app, db = app_db
    r = app.app.test_client().post("/api/people", json=[LOWER],
                                   headers={"X-Console-Key": "testkey"})
    assert r.status_code == 200
    assert _names(db, "hvh@x.com") == FIXED


def test_a_name_somebody_chose_in_mixed_case_is_kept(app_db):
    app, db = app_db
    app.app.test_client().post("/api/people?merge_tags=1",
                               json=[{"email": "d@x.com", "name": "DeAnna smith"}],
                               headers={"X-Console-Key": "testkey"})
    assert _names(db, "d@x.com")[0] == "DeAnna smith"


def _mem_db():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
               "first_name TEXT, last_name TEXT, phone TEXT, source TEXT, roles TEXT, "
               "created_at TEXT, updated_at TEXT)")
    cx.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
               "updated_at TEXT)")
    return cx


def test_order_entry_creating_a_person_capitalises():
    from dashboard import customers as C
    cx = _mem_db()
    pid = C.find_or_create_by_email(cx, email="as@x.com", name="aaron soto")
    assert pid
    assert cx.execute("SELECT name FROM people WHERE id=?", (pid,)).fetchone()[0] == \
        "Aaron Soto"


def test_a_console_rename_capitalises_person_and_orders():
    from dashboard import customers as C
    cx = _mem_db()
    cx.execute("INSERT INTO people (email, name) VALUES ('m@x.com', 'x')")
    cx.execute("INSERT INTO orders (email, name) VALUES ('m@x.com', 'x')")
    C.rename_by_email(cx, "m@x.com", name="maria van der vegt",
                      first_name="maria", last_name="van der vegt")
    p = cx.execute("SELECT name, first_name, last_name FROM people").fetchone()
    assert tuple(p) == ("Maria van der Vegt", "Maria", "van der Vegt")
    assert cx.execute("SELECT name FROM orders").fetchone()[0] == "Maria van der Vegt"


def test_a_portal_holder_created_lazily_is_capitalised():
    from dashboard import portal_identity as P
    cx = _mem_db()
    pid, roles = P._get_or_create_person(cx, "c@x.com", "celeste o'brien")
    assert roles == ["client"]
    assert cx.execute("SELECT name FROM people WHERE id=?", (pid,)).fetchone()[0] == \
        "Celeste O'Brien"
