"""The person merge engine: preview, apply, undo and the sweep.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import json
import os
import sqlite3

import pytest

from dashboard import db
from dashboard import person_aliases as pa
from dashboard import person_merge as pm

MERGE_TABLES = ("person_merges", "email_aliases", "portal_token_aliases", "person_merge_changes")
DDL = [
    "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, tags TEXT)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, person_id INTEGER, total REAL)",
    "CREATE TABLE carts (email TEXT PRIMARY KEY, items TEXT)",
    "CREATE TABLE reveals (id INTEGER PRIMARY KEY, email TEXT, scan_date TEXT, "
    "UNIQUE(email, scan_date))",
    "CREATE TABLE coach_threads (id INTEGER PRIMARY KEY, coach_email TEXT, member_email TEXT)",
    "CREATE TABLE ghl_write_queue (id INTEGER PRIMARY KEY, email TEXT)",
    "CREATE TABLE client_portals (id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE, email TEXT, "
    "name TEXT, content_json TEXT, created_at TEXT, updated_at TEXT)",
    "CREATE TABLE portal_notify_state (email TEXT PRIMARY KEY, portal_token TEXT)",
]
DATA_TABLES = ("people", "orders", "carts", "reveals", "coach_threads", "ghl_write_queue",
               "client_portals", "portal_notify_state")
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


def _seed(cx):
    cx.execute("INSERT INTO people VALUES (1, ?, 'Mel Palmer', '[\"a\"]')", (AOL,))
    cx.execute("INSERT INTO people VALUES (2, ?, '', '[\"b\"]')", (GMAIL,))
    cx.execute("INSERT INTO orders VALUES (10, ?, 1, 50.0)", (AOL,))
    cx.execute("INSERT INTO orders VALUES (11, ' Mel@AOL.com', NULL, 20.0)")
    cx.execute("INSERT INTO orders VALUES (12, ?, 2, 5.0)", (GMAIL,))
    cx.execute("INSERT INTO carts VALUES (?, 'aol-cart')", (AOL,))
    cx.execute("INSERT INTO carts VALUES (?, 'gmail-cart')", (GMAIL,))
    cx.execute("INSERT INTO reveals VALUES (20, ?, '2026-07-25')", (AOL,))
    cx.execute("INSERT INTO reveals VALUES (21, ?, '2026-09-19')", (AOL,))
    cx.execute("INSERT INTO reveals VALUES (22, ?, '2026-07-25')", (GMAIL,))
    cx.execute("INSERT INTO coach_threads VALUES (30, ?, 'kai@x.com')", (AOL,))
    cx.execute("INSERT INTO ghl_write_queue VALUES (40, ?)", (AOL,))
    cx.execute("INSERT INTO client_portals VALUES (50, 'hash-aol', ?, 'Mel', ?, 't', "
               "'2026-09-19T00:00:00')",
               (AOL, json.dumps({"greeting": "Sept", "layers": [1, 2], "phase": "fire"})))
    cx.execute("INSERT INTO client_portals VALUES (51, 'hash-gmail', ?, 'Mel', ?, 't', "
               "'2026-07-25T00:00:00')",
               (GMAIL, json.dumps({"greeting": "July", "layers": [9], "schedule": "Mon"})))
    cx.execute("INSERT INTO portal_notify_state VALUES (?, 'raw-aol')", (AOL,))
    cx.execute("INSERT INTO portal_notify_state VALUES (?, 'raw-gmail')", (GMAIL,))
    cx.commit()


def _fields(cx, survivor_id, merged_id):
    """Stand-in for app._merge_two_people: fill empty fields, union tags, delete."""
    s = cx.execute("SELECT name, tags FROM people WHERE id=?", (survivor_id,)).fetchone()
    m = cx.execute("SELECT name, tags FROM people WHERE id=?", (merged_id,)).fetchone()
    name = s[0] or m[0]
    tags = sorted(set(json.loads(s[1] or "[]")) | set(json.loads(m[1] or "[]")))
    cx.execute("UPDATE people SET name=?, tags=? WHERE id=?", (name, json.dumps(tags), survivor_id))
    cx.execute("DELETE FROM people WHERE id=?", (merged_id,))


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    for d in DDL:
        c.execute(d)
    pa.init_tables(c)
    _seed(c)
    return c


def _dump(cx, tables=DATA_TABLES):
    out = {}
    for t in tables:
        rows = cx.execute(f'SELECT * FROM "{t}"').fetchall()
        out[t] = sorted(repr(tuple(r)) for r in rows)
    return out


def _apply(cx, **kw):
    args = dict(survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                applied_by="test", merge_people_fields=_fields)
    args.update(kw)
    mid = pm.apply(cx, **args)
    cx.commit()
    return mid


def test_preview_writes_nothing(cx):
    before = _dump(cx, DATA_TABLES + MERGE_TABLES)
    p = pm.preview(cx, 2, 1)
    assert p["counts"]["orders"] == [2, 1]
    assert p["clashes"]["carts"] == 1
    assert p["portal"]["keep"] == "merged"
    assert p["portal"]["from_other"] == ["schedule"]
    assert _dump(cx, DATA_TABLES + MERGE_TABLES) == before


def test_apply_moves_rows_and_sets_aside_clashes(cx):
    mid = _apply(cx)
    assert {r[0] for r in cx.execute("SELECT email FROM orders")} == {GMAIL}
    assert cx.execute("SELECT person_id FROM orders WHERE id=10").fetchone()[0] == 2
    assert cx.execute("SELECT items FROM carts").fetchall() == [("gmail-cart",)]
    reveals = cx.execute("SELECT id, email FROM reveals ORDER BY id").fetchall()
    assert reveals == [(21, GMAIL), (22, GMAIL)]
    aside = cx.execute("SELECT table_name FROM person_merge_changes WHERE merge_id=? AND "
                       "action='set_aside'", (mid,)).fetchall()
    assert {t for (t,) in aside} >= {"carts", "reveals", "client_portals", "portal_notify_state"}
    assert pa.canonical_email(cx, AOL) == GMAIL
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1


def test_history_tables_untouched(cx):
    _apply(cx)
    assert cx.execute("SELECT email FROM ghl_write_queue").fetchone()[0] == AOL


def test_case_and_space_variants_move(cx):
    _apply(cx)
    assert cx.execute("SELECT email FROM orders WHERE id=11").fetchone()[0] == GMAIL


def test_two_columns_on_one_row(cx):
    _apply(cx)
    assert cx.execute("SELECT coach_email, member_email FROM coach_threads").fetchall() == [
        (GMAIL, "kai@x.com")]


def test_newer_portal_is_kept_and_both_links_work(cx):
    _apply(cx)
    rows = cx.execute("SELECT id, email, token_hash FROM client_portals").fetchall()
    assert rows == [(50, GMAIL, "hash-aol")]
    assert pa.token_alias(cx, "hash-gmail") == GMAIL
    assert cx.execute("SELECT portal_token FROM portal_notify_state WHERE email=?",
                      (GMAIL,)).fetchone()[0] == "raw-aol"


def test_portal_content_combines_by_rule(cx):
    _apply(cx)
    content = json.loads(cx.execute("SELECT content_json FROM client_portals").fetchone()[0])
    assert content == {"greeting": "Sept", "layers": [1, 2], "phase": "fire", "schedule": "Mon"}


def test_apply_twice_is_refused(cx):
    mid = _apply(cx)
    n = cx.execute("SELECT COUNT(*) FROM person_merge_changes").fetchone()[0]
    cx.execute("INSERT INTO people VALUES (1, ?, 'again', '[]')", (AOL,))
    with pytest.raises(pm.MergeRefused):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)
    assert cx.execute("SELECT COUNT(*) FROM person_merge_changes").fetchone()[0] == n
    assert mid


def test_bad_mail_choice_and_same_person_are_refused(cx):
    with pytest.raises(pm.MergeRefused):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="maybe", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)
    with pytest.raises(pm.MergeRefused):
        pm.apply(cx, survivor_id=2, merged_id=2, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=_fields)


def test_undo_restores_the_database_exactly(cx):
    before = _dump(cx)
    mid = _apply(cx)
    report = pm.undo(cx, mid, "test")
    cx.commit()
    assert report["skipped"] == []
    assert _dump(cx) == before
    assert pa.canonical_email(cx, AOL) == AOL
    assert pa.token_alias(cx, "hash-gmail") is None
    assert cx.execute("SELECT undone_at FROM person_merges WHERE id=?", (mid,)).fetchone()[0]


def test_undo_twice_is_refused(cx):
    mid = _apply(cx)
    pm.undo(cx, mid, "t")
    cx.commit()
    with pytest.raises(pm.MergeRefused):
        pm.undo(cx, mid, "t")


def test_undo_skips_a_row_changed_since(cx):
    mid = _apply(cx)
    cx.execute("UPDATE orders SET email='someone@else.com' WHERE id=10")
    report = pm.undo(cx, mid, "t")
    assert any(s["table"] == "orders" for s in report["skipped"])
    assert cx.execute("SELECT email FROM orders WHERE id=11").fetchone()[0] == " Mel@AOL.com"


def test_undo_refused_while_a_later_merge_depends(cx):
    first = _apply(cx)
    cx.execute("INSERT INTO people VALUES (3, 'mel@proton.me', 'Mel', '[]')")
    second = _apply(cx, survivor_id=3, merged_id=2)
    with pytest.raises(pm.MergeRefused) as e:
        pm.undo(cx, first, "t")
    assert str(second) in str(e.value)


def test_failure_mid_apply_leaves_nothing(cx):
    before = _dump(cx, DATA_TABLES + MERGE_TABLES)

    def boom(*a):
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                 applied_by="t", merge_people_fields=boom)
    cx.rollback()
    assert _dump(cx, DATA_TABLES + MERGE_TABLES) == before


def test_sweep_moves_late_rows_once(cx):
    mid = _apply(cx)
    cx.execute("INSERT INTO orders VALUES (13, ?, NULL, 9.0)", (AOL,))
    assert pm.sweep(cx) == 1
    assert cx.execute("SELECT email FROM orders WHERE id=13").fetchone()[0] == GMAIL
    src = cx.execute("SELECT source, merge_id FROM person_merge_changes WHERE table_name='orders' "
                     "AND key_json LIKE '%13%'").fetchone()
    assert src == ("sweep", mid)
    assert pm.sweep(cx) == 0


@pytest.mark.skipif(not os.environ.get("PG_DSN"), reason="PG_DSN not set")
def test_round_trip_on_postgres(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/pm_engine_test.db")
    try:
        for t in DATA_TABLES + MERGE_TABLES:
            c.execute(f'DROP TABLE IF EXISTS "{t}"')
        for d in DDL:
            c.execute(d)
        pa.init_tables(c)
        _seed(c)
        before = _dump(c)
        mid = _apply(c)
        assert pa.canonical_email(c, AOL) == GMAIL
        assert c.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
        report = pm.undo(c, mid, "t")
        c.commit()
        assert report["skipped"] == []
        assert _dump(c) == before
    finally:
        c.rollback()
        for t in DATA_TABLES + MERGE_TABLES:
            c.execute(f'DROP TABLE IF EXISTS "{t}"')
        c.commit()
        c.close()
