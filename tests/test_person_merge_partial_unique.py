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
    "CREATE TABLE cart_items (token TEXT NOT NULL, slug TEXT NOT NULL, fmt TEXT NOT NULL DEFAULT '', "
    "qty INTEGER NOT NULL, source TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, "
    "PRIMARY KEY (token, slug, fmt))",
]


def _seed(cx):
    cx.execute("INSERT INTO people VALUES (1, ?, 'Peach Goddard', '[]')", (AOL,))
    cx.execute("INSERT INTO people VALUES (2, ?, 'Peach Goddard', '[]')", (GMAIL,))
    cx.execute("INSERT INTO carts VALUES ('t-aol-open', ?, 'open', '2026-09-20')", (AOL,))
    cx.execute("INSERT INTO carts VALUES ('t-aol-old', ?, 'ordered', '2026-01-01')", (AOL,))
    cx.execute("INSERT INTO carts VALUES ('t-gm-open', ?, 'open', '2026-09-25')", (GMAIL,))
    for tok, slug, qty in (("t-aol-open", "kelp", 1), ("t-aol-open", "zinc", 3),
                           ("t-gm-open", "zinc", 2), ("t-gm-open", "iodine", 1),
                           ("t-aol-old", "old", 1)):
        cx.execute("INSERT INTO cart_items VALUES (?, ?, '', ?, 'shop', '2026-09-01')",
                   (tok, slug, qty))
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


def _items(c):
    return sorted(tuple(r) for r in c.execute("SELECT token, slug, qty FROM cart_items").fetchall())


def _carts(c):
    return sorted(tuple(r) for r in c.execute("SELECT token, email, status FROM carts").fetchall())


def test_partial_unique_sets_reads_the_predicate(cx):
    got = pd.partial_unique_sets(cx, None, "carts")
    assert len(got) == 1 and got[0][0] == ("email",) and "open" in got[0][1]


def test_preview_shows_the_open_cart_clash_and_its_rule(cx):
    p = pm.preview(cx, 2, 1)
    assert p["blocked"] == []
    assert p["clashes"]["carts"] == {"count": 1, "rule": "fold_cart:updated_at"}


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
        for t in ("people", "carts", "cart_items", "person_merges", "email_aliases",
                  "portal_token_aliases", "person_merge_changes"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()
        _build(c)
        got = pd.partial_unique_sets(c, pm._own_schema(c), "carts")
        assert len(got) == 1 and got[0][0] == ("email",)
        assert pm.preview(c, 2, 1)["clashes"]["carts"]["rule"] == "fold_cart:updated_at"
        before = (_items(c), _carts(c))
        mid = _apply(c)
        open_ = [r for r in _carts(c) if r[2] == "open" and r[1] == GMAIL]
        assert [r[0] for r in open_] == ["t-gm-open"]
        assert ("t-gm-open", "zinc", 3) in _items(c) and ("t-gm-open", "kelp", 1) in _items(c)
        pm.undo(c, mid, undone_by="test")
        c.commit()
        assert (_items(c), _carts(c)) == before
    finally:
        c.rollback()
        for t in ("people", "carts", "cart_items", "person_merges", "email_aliases",
                  "portal_token_aliases", "person_merge_changes"):
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        c.commit()


# ── Glen, 2026-09-27: "fold" ─────────────────────────────────────────────────
# Two open carts: the newest stays open and the other's items fold into it, the higher
# quantity winning for a product in both (the shop's own fold rule). Nothing is summed.
def test_the_older_open_carts_items_fold_into_the_kept_one(cx):
    _apply(cx)
    got = _items(cx)
    assert ("t-gm-open", "kelp", 1) in got                  # from the older cart
    assert ("t-gm-open", "zinc", 3) in got                  # higher quantity wins
    assert ("t-gm-open", "iodine", 1) in got
    assert not [r for r in got if r[0] == "t-aol-open"]     # the folded cart holds nothing
    assert ("t-aol-old", "old", 1) in got                   # a closed cart's items are untouched


def test_the_fold_keeps_the_kept_carts_higher_quantity(cx):
    cx.execute("UPDATE cart_items SET qty=9 WHERE token='t-gm-open' AND slug='zinc'")
    cx.commit()
    _apply(cx)
    assert ("t-gm-open", "zinc", 9) in _items(cx)


def test_undo_puts_every_item_back_in_its_own_cart(cx):
    before_items, before_carts = _items(cx), _carts(cx)
    mid = _apply(cx)
    pm.undo(cx, mid, undone_by="test")
    cx.commit()
    assert _items(cx) == before_items
    assert _carts(cx) == before_carts


# ── Review rounds 1 and 2 ────────────────────────────────────────────────────
def test_the_condition_is_judged_on_the_moved_row(cx):
    """Round 2: a condition on the address itself must be read with the NEW address."""
    cx.execute("CREATE TABLE holds (id INTEGER PRIMARY KEY, email TEXT, kind TEXT, updated_at TEXT)")
    cx.execute(f"CREATE UNIQUE INDEX ux_holds ON holds(email) WHERE email='{GMAIL}'")
    cx.execute("INSERT INTO holds VALUES (1, ?, 'x', '2026-01-01')", (AOL,))
    cx.execute("INSERT INTO holds VALUES (2, ?, 'x', '2026-02-01')", (GMAIL,))
    cx.commit()
    t = pd.Target(None, "holds", "email", "email")
    row = dict(zip(("id", "email", "kind", "updated_at"),
                   cx.execute("SELECT * FROM holds WHERE id=1").fetchone()))
    got = pm._clash_row(cx, t, row, GMAIL)
    assert got is not None and got["id"] == 2


def test_sqlite_index_with_a_trailing_comment_is_read(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "c.db"))
    c.execute("CREATE TABLE k (id INTEGER PRIMARY KEY, email TEXT, status TEXT)")
    c.execute("CREATE UNIQUE INDEX ux_k ON k(email) WHERE status='open' -- one open\n")
    got = pd.partial_unique_sets(c, None, "k")
    assert got == [(("email",), "status='open'")]
    c.execute(f"SELECT 1 FROM k WHERE ({got[0][1]})").fetchall()      # usable as SQL


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_postgres_include_columns_are_not_part_of_the_key(monkeypatch):
    """Round 2: an INCLUDE column is stored in the index, not part of what must be unique."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    c = db.connect("/data/pm_partial_test.db")
    try:
        c.execute('DROP TABLE IF EXISTS "inc_demo" CASCADE')
        c.execute("CREATE TABLE inc_demo (id INTEGER PRIMARY KEY, email TEXT, status TEXT, "
                  "updated_at TEXT)")
        c.execute("CREATE UNIQUE INDEX ux_inc_demo ON inc_demo(email) INCLUDE (updated_at) "
                  "WHERE status='open'")
        got = pd.partial_unique_sets(c, pm._own_schema(c), "inc_demo")
        assert [g[0] for g in got] == [("email",)]
    finally:
        c.rollback()
        c.execute('DROP TABLE IF EXISTS "inc_demo" CASCADE')
        c.commit()


# ── Review round 3 ───────────────────────────────────────────────────────────
def test_the_folded_cart_is_marked_merged_not_deleted(cx):
    """Round 3: the shop marks a folded cart 'merged'. Deleting it let a signed-out browser
    recreate an empty cart under the same token, and undo then failed on that token."""
    _apply(cx)
    assert ("t-aol-open", GMAIL, "merged") in _carts(cx) or ("t-aol-open", AOL, "merged") in _carts(cx)


def test_undo_leaves_a_checked_out_cart_alone(cx):
    """Round 3: undo must not take items out of a cart that has been ordered since."""
    mid = _apply(cx)
    cx.execute("UPDATE carts SET status='ordered' WHERE token='t-gm-open'")
    cx.commit()
    ordered = [r for r in _items(cx) if r[0] == "t-gm-open"]
    res = pm.undo(cx, mid, undone_by="test")
    cx.commit()
    assert [r for r in _items(cx) if r[0] == "t-gm-open"] == ordered
    assert not [r for r in _items(cx) if r[0] == "t-aol-open"]     # not copied back either
    assert any(s["table"] == "cart_items" for s in res["skipped"])
