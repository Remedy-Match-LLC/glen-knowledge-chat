"""Which tables and columns a person merge moves.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import os
import sqlite3

import pytest

from dashboard import db
from dashboard import person_merge_discover as pd

DDL = [
    "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, tags TEXT)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, person_id INTEGER, total REAL)",
    "CREATE TABLE carts (email TEXT PRIMARY KEY, items TEXT)",
    "CREATE TABLE reveals (id INTEGER PRIMARY KEY, email TEXT, scan_date TEXT, "
    "UNIQUE(email, scan_date))",
    "CREATE TABLE coach_threads (id INTEGER PRIMARY KEY, coach_email TEXT, member_email TEXT)",
    "CREATE TABLE ghl_write_queue (id INTEGER PRIMARY KEY, email TEXT)",
    "CREATE TABLE notes (body TEXT)",
]
TABLES = ("people", "orders", "carts", "reveals", "coach_threads", "ghl_write_queue", "notes")


def _check(cx):
    got = {(t.table, t.column, t.kind) for t in pd.targets(cx) if t.table in TABLES}
    assert ("orders", "email", "email") in got
    assert ("orders", "person_id", "person") in got
    assert ("coach_threads", "coach_email", "email") in got
    assert ("coach_threads", "member_email", "email") in got
    assert ("carts", "email", "email") in got
    assert ("reveals", "email", "email") in got
    assert ("ghl_write_queue", "email", "email") in got
    assert not any(t == "notes" for t, _, _ in got)
    assert ("email", "scan_date") in pd.unique_sets(cx, "reveals")
    assert ("email",) in pd.unique_sets(cx, "carts")
    assert pd.key_columns(cx, "orders") == ["id"]
    assert pd.key_columns(cx, "notes") == ["body"]
    assert "ghl_write_queue" in pd.HISTORY_TABLES
    assert "email_suppression" in pd.HISTORY_TABLES


def test_discovery_on_sqlite(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    for d in DDL:
        cx.execute(d)
    _check(cx)


@pytest.mark.skipif(not os.environ.get("PG_DSN"), reason="PG_DSN not set")
def test_discovery_on_postgres(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    cx = db.connect("/data/pm_discover_test.db")
    try:
        for t in TABLES:
            cx.execute(f'DROP TABLE IF EXISTS "{t}"')
        for d in DDL:
            cx.execute(d)
        cx.commit()
        _check(cx)
    finally:
        for t in TABLES:
            cx.execute(f'DROP TABLE IF EXISTS "{t}"')
        cx.commit()
        cx.close()
