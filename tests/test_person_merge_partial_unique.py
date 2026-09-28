"""A merge must see a clash that only a PARTIAL unique index enforces.

2026-09-27: Peach Goddard's merge was refused three times with "clashes with no written rule
in: carts". Each address held one open cart, and production allows one open cart per
address (ux_carts_open_email ... WHERE status='open'). The engine only looked for clashes
under plain unique indexes, so the preview showed none and the written carts rule (newest
wins) never ran; the database refused the move instead. The engine's own test fixture gave
carts an email primary key, which is not the production shape."""
import json
import os

import pytest

from dashboard import db
from dashboard import person_aliases as pa
from dashboard import person_merge as pm
from dashboard import person_merge_discover as pd

AOL, GMAIL = "p@aol.com", "p@gmail.com"
DDL = [
    "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, tags TEXT)",
    # The production shape (dashboard/cart_store.py).
    "CREATE TABLE carts (token TEXT PRIMARY KEY, email TEXT, status TEXT, updated_at TEXT)",
    "CREATE UNIQUE INDEX ux_carts_open_email ON carts(email) WHERE status='open' AND email<>''",
]


def _seed(cx):
    cx.execute("INSERT INTO people VALUES (1, ?, 'Peach Goddard', '[]')", (AOL,))
    cx.execute("INSERT INTO people VALUES (2, ?, 'Peach Goddard', '[]')", (GMAIL,))
    cx.execute("INSERT INTO carts VALUES ('t-aol-open', ?, 'open', '2026-09-20')", (AOL,))
    cx.execute("INSERT INTO carts VALUES ('t-aol-old', ?, 'ordered', '2026-01-01')", (AOL,))
    cx.execute("INSERT INTO carts VALUES ('t-gm-open', ?, 'open', '2026-09-25')", (GMAIL,))
    cx.commit()


def _fields(cx, survivor_id, merged_id):
    cx.execute("DELETE FROM people WHERE id=?", (merged_id,))


def _build(c):
    for d in DDL:
        c.execute(d)
    pa.init_tables(c)
    _seed(c)
    return c


@pytest.fixture
def cx(tmp_path):
    import sqlite3
    return _build(sqlite3.connect(str(tmp_path / "t.db")))


def _apply(c):
    mid = pm.apply(c, survivor_id=2, merged_id=1, mail_old="keep", evidence={}, suggestion={},
                   applied_by="test", merge_people_fields=_fields)
    c.commit()
    return mid


def _carts(c):
    return sorted(tuple(r) for r in c.execute("SELECT token, email, status FROM carts").fetchall())


def test_partial_unique_sets_reads_the_predicate(cx):
    got = pd.partial_unique_sets(cx, None, "carts")
    assert len(got) == 1 and got[0][0] == ("email",) and "open" in got[0][1]


def test_preview_shows_the_open_cart_clash_and_its_rule(cx):
    p = pm.preview(cx, 2, 1)
    assert p["blocked"] == []
    assert p["clashes"]["carts"] == {"count": 1, "rule": "newest:updated_at"}


def test_apply_settles_open_carts_by_the_written_rule(cx):
    _apply(cx)
    carts = _carts(cx)
    open_ = [c for c in carts if c[2] == "open" and c[1] == GMAIL]
    assert [c[0] for c in open_] == ["t-gm-open"]          # the newer open cart stays
    assert ("t-aol-old", GMAIL, "ordered") in carts         # a closed cart just moves


def test_apply_keeps_the_merged_open_cart_when_it_is_newer(cx):
    cx.execute("UPDATE carts SET updated_at='2026-09-27' WHERE token='t-aol-open'")
    cx.commit()
    _apply(cx)
    open_ = [c for c in _carts(cx) if c[2] == "open" and c[1] == GMAIL]
    assert [c[0] for c in open_] == ["t-aol-open"]


def test_undo_restores_both_open_carts(cx):
    before = _carts(cx)
    mid = _apply(cx)
    pm.undo(cx, mid, undone_by="test")
    cx.commit()
    assert _carts(cx) == before


def _pg_ok():
    dsn = os.environ.get("PG_DSN", "")
    return bool(dsn) and "test" in dsn          # never a real database


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_open_cart_clash_on_postgres(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/pm_partial_test.db")
    try:
        for t in ("people", "carts", "person_merges", "email_aliases", "portal_token_aliases",
                  "person_merge_changes"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
        _build(c)
        got = pd.partial_unique_sets(c, pm._own_schema(c), "carts")
        assert len(got) == 1 and got[0][0] == ("email",)
        assert pm.preview(c, 2, 1)["clashes"]["carts"]["rule"] == "newest:updated_at"
        _apply(c)
        open_ = [r for r in _carts(c) if r[2] == "open" and r[1] == GMAIL]
        assert [r[0] for r in open_] == ["t-gm-open"]
    finally:
        c.rollback()
        for t in ("people", "carts", "person_merges", "email_aliases", "portal_token_aliases",
                  "person_merge_changes"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
