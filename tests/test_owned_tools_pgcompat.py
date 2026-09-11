"""owned_tools.add must not read cur.lastrowid, and must not invent an id on a dedupe.

`add` backs the portal's "add to your Oasis" button. On Postgres it 500s on the
first add of any new tool, the same defect that broke the supplement stack on
2026-09-10:

    return {"created": True, "id": cur.lastrowid}
    AttributeError: lastrowid is unavailable on the Postgres backend

There is a second, quieter bug underneath it. The statement is `INSERT OR IGNORE`,
so on a repeat add SQLite inserts nothing and `lastrowid` still holds the id of
whatever this connection inserted LAST. Today `cur.rowcount` happens to guard that.
Anything that returns an id without checking whether a row was actually written
would hand back somebody else's row.
"""
import sqlite3

import pytest

from dashboard import db
from dashboard import dbwrite
from dashboard import owned_tools as ot


class _PgLikeConn:
    """sqlite underneath, answering like the Postgres adapter.

    `.lastrowid` raises exactly as `dashboard/db.py::_PgCursor` does. That is the
    production behaviour being modelled: a fake that returned None instead would
    let the bug through and still pass.
    """

    backend = "postgres"

    def __init__(self):
        self._cx = sqlite3.connect(":memory:")

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        @property
        def lastrowid(self):
            raise AttributeError(
                "lastrowid is unavailable on the Postgres backend; "
                "use 'INSERT ... RETURNING id' and read fetchone()[0]")

        @property
        def rowcount(self):
            return self._cur.rowcount

        def fetchone(self):
            return self._cur.fetchone()

        def fetchall(self):
            return self._cur.fetchall()

    def execute(self, sql, params=()):
        return self._Cur(self._cx.execute(sql, params))

    def commit(self):
        return self._cx.commit()

    def close(self):
        return self._cx.close()


@pytest.fixture
def pg_like():
    cx = _PgLikeConn()
    ot.init_table(cx)
    yield cx
    cx.close()


@pytest.fixture
def lite():
    cx = sqlite3.connect(":memory:")
    ot.init_table(cx)
    yield cx
    cx.close()


# --- the reported defect -------------------------------------------------------

def test_add_returns_the_new_id_on_postgres(pg_like):
    out = ot.add(pg_like, "glen@example.com", "Living Water ionizer", brand="Remedy Match")
    assert out["created"] is True
    assert isinstance(out["id"], int) and out["id"] > 0, out


def test_the_returned_id_addresses_the_row_it_claims(pg_like):
    out = ot.add(pg_like, "glen@example.com", "Living Water ionizer")
    row = pg_like.execute("SELECT email, name FROM owned_tools WHERE id=?",
                          (out["id"],)).fetchone()
    assert row is not None, "the returned id matches no row"
    assert row[0] == "glen@example.com"
    assert row[1] == "Living Water ionizer"


def test_adding_the_same_tool_twice_is_idempotent(pg_like):
    first = ot.add(pg_like, "glen@example.com", "Living Water ionizer")
    second = ot.add(pg_like, "glen@example.com", "Living Water ionizer")
    assert second["created"] is False
    assert second["id"] == first["id"]


def test_a_dedupe_never_returns_another_rows_id(pg_like):
    """The quiet bug. A second client's insert must not become the first's id."""
    mine = ot.add(pg_like, "glen@example.com", "Living Water ionizer")
    ot.add(pg_like, "someone@example.com", "Something else entirely")
    again = ot.add(pg_like, "glen@example.com", "Living Water ionizer")
    assert again["created"] is False
    assert again["id"] == mine["id"], "the dedupe returned the other insert's id"


def test_sqlite_still_works(lite):
    """The control. The Postgres branch must not be the only one that works."""
    first = ot.add(lite, "glen@example.com", "Living Water ionizer")
    assert first["created"] is True and first["id"] > 0
    ot.add(lite, "someone@example.com", "Something else entirely")
    again = ot.add(lite, "glen@example.com", "Living Water ionizer")
    assert again["created"] is False
    assert again["id"] == first["id"]


def test_module_reads_no_lastrowid_anywhere():
    """Guards the file rather than the one line, as supplement_reviews now does."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ot))
    offenders = [n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and n.attr == "lastrowid"]
    assert not offenders, f"lastrowid is back in owned_tools.py: {offenders}"


# --- the shared helper, which owned_tools is the first caller to stress ---------

def test_helper_returns_none_when_no_row_was_inserted_sqlite(tmp_path):
    """`INSERT OR IGNORE` that ignores must not report the previous insert's id.

    Nothing called the helper with a conflicting statement before owned_tools, so
    this path had never run. On SQLite `lastrowid` is sticky and would answer with
    a stale, valid-looking id.
    """
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT UNIQUE)")
    first = dbwrite.insert_returning_id(cx, "INSERT OR IGNORE INTO t (k) VALUES (?)", ("a",))
    assert first == 1
    dbwrite.insert_returning_id(cx, "INSERT OR IGNORE INTO t (k) VALUES (?)", ("b",))
    dupe = dbwrite.insert_returning_id(cx, "INSERT OR IGNORE INTO t (k) VALUES (?)", ("a",))
    assert dupe is None, f"a dedupe returned id {dupe}, which is another row"
    cx.close()


def test_helper_still_returns_the_id_on_a_real_insert_sqlite(tmp_path):
    """The control for the test above, so 'always None' cannot pass."""
    cx = sqlite3.connect(str(tmp_path / "t2.db"))
    cx.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT UNIQUE)")
    assert dbwrite.insert_returning_id(cx, "INSERT INTO t (k) VALUES (?)", ("a",)) == 1
    assert dbwrite.insert_returning_id(cx, "INSERT OR IGNORE INTO t (k) VALUES (?)", ("b",)) == 2
    cx.close()


def test_pgcompat_puts_on_conflict_before_the_returning():
    """If the splice ever lands the other way the statement is a syntax error."""
    from dashboard import pgcompat
    out = pgcompat.translate_sql(
        "INSERT OR IGNORE INTO owned_tools (email) VALUES (?) RETURNING id").upper()
    assert "OR IGNORE" not in out
    assert out.index("ON CONFLICT") < out.index("RETURNING")
