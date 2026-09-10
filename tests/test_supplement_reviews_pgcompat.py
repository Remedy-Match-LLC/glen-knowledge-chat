"""supplement_reviews.init_table must not issue PRAGMA on Postgres.

It did, at `PRAGMA table_info(supplement_reviews)`. pgcompat passes that through
unchanged, Postgres raises a syntax error, and the transaction aborts. Every portal
intake POST for the free product review feature returned 500 in production:

    psycopg.errors.SyntaxError: syntax error at or near "PRAGMA"
    LINE 1: PRAGMA table_info(supplement_reviews)

Observed live 2026-09-09 on /api/portal/<token>/remedies/add, /remedies/meta and
/remedies/remove. dashboard/product_ratings.py carries a comment describing the same
failure, so this is the second time it has bitten.
"""
import sqlite3

import pytest

from dashboard import db
from dashboard import supplement_reviews as sr


class _RecordingConn:
    """A sqlite connection that reports itself as Postgres and records every SQL
    string it is handed. Lets the DDL actually run while proving which dialect of
    introspection init_table chose."""

    backend = "postgres"

    def __init__(self):
        self._cx = sqlite3.connect(":memory:")
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self._cx.execute(sql, params)

    def commit(self):
        return self._cx.commit()

    def close(self):
        return self._cx.close()


def test_init_table_issues_no_pragma_on_postgres(monkeypatch):
    # The real column check would hit information_schema, which sqlite lacks. The
    # point of the test is which branch is taken, so stand the answer in.
    seen = []

    def fake_column_exists(cx, table, column):
        seen.append((table, column))
        return True

    monkeypatch.setattr(db, "column_exists", fake_column_exists)

    cx = _RecordingConn()
    try:
        sr.init_table(cx)
    finally:
        cx.close()

    offenders = [s for s in cx.sql if "PRAGMA" in s.upper()]
    assert not offenders, f"init_table issued PRAGMA on a Postgres connection: {offenders}"
    assert ("supplement_reviews", "reason") in seen
    assert ("supplement_reviews", "importance") in seen


def test_init_table_still_works_on_sqlite():
    """The real path, unmocked: sqlite must still get both late columns."""
    cx = sqlite3.connect(":memory:")
    try:
        sr.init_table(cx)
        cols = {r[1] for r in cx.execute("PRAGMA table_info(supplement_reviews)")}
        assert "reason" in cols
        assert "importance" in cols
        # Re-running must be a no-op rather than a duplicate-column error.
        sr.init_table(cx)
    finally:
        cx.close()


def test_module_executes_no_pragma_anywhere():
    """Guards the whole module, not just the line that broke.

    Walks the AST for cx.execute(...) calls carrying a literal PRAGMA, so a comment
    or docstring naming PRAGMA does not trip it and a second offending call cannot
    slip in somewhere else in the file.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sr))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "execute"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and "PRAGMA" in arg.value.upper():
                offenders.append(arg.value.strip()[:60])
    assert not offenders, f"a PRAGMA is back in supplement_reviews.py: {offenders}"


# --- cur.lastrowid is the same class of bug, found live 2026-09-10 -------------
#
# Glen added "Crystalline Clarity" from his portal and got "Something went wrong.
# Please try again.". The row WAS inserted and committed; the 500 came afterwards,
# reading the new id:
#
#   File "dashboard/supplement_reviews.py", line 186, in add_listed
#     return {"created": True, "id": cur.lastrowid, "status": "listed"}
#   AttributeError: lastrowid is unavailable on the Postgres backend;
#     use 'INSERT ... RETURNING id' and read fetchone()[0]
#
# Third time a SQLite idiom has reached Postgres in this one module, so the last
# test here guards the whole file rather than the two lines that broke.


class _PgLikeConn:
    """sqlite underneath, but it answers like the Postgres adapter.

    The cursor raises on `.lastrowid` exactly as `dashboard/db.py::_PgCursor` does.
    That is the production behaviour being modelled, not a convenience: a fake that
    quietly returned None would let the bug through and still pass.
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
def pg_like(monkeypatch):
    # Answer column_exists off the real sqlite table underneath. Standing a blanket
    # True in here would skip the late ADD COLUMNs and the INSERT would then fail on
    # a missing column, which is a fixture bug wearing the costume of a real one.
    def real_column_exists(cx, table, column):
        return column in {r[1] for r in cx._cx.execute(f"PRAGMA table_info({table})")}

    monkeypatch.setattr(db, "column_exists", real_column_exists)
    cx = _PgLikeConn()
    sr.init_table(cx)
    yield cx
    cx.close()


def test_the_fixture_really_has_the_late_columns(pg_like):
    """Guards the fixture itself, after it lied once and hid a passing fix."""
    cols = {r[1] for r in pg_like._cx.execute("PRAGMA table_info(supplement_reviews)")}
    assert "reason" in cols and "importance" in cols, cols


def test_add_listed_returns_the_new_id_on_postgres(pg_like):
    """The reported bug. Before the fix this raised AttributeError and the route 500'd."""
    out = sr.add_listed(pg_like, "glen@example.com", "Crystalline Clarity",
                        product_brand="Remedy Match", source="portal-catalog")
    assert out["created"] is True
    assert out["status"] == "listed"
    assert isinstance(out["id"], int) and out["id"] > 0, out


def test_create_request_returns_the_new_id_on_postgres(pg_like):
    """create_request carries the identical line and is reachable from the same panel."""
    out = sr.create_request(pg_like, "glen@example.com", "Clarity",
                            product_brand="Remedy Match")
    assert out["created"] is True
    assert out["status"] == "requested"
    assert isinstance(out["id"], int) and out["id"] > 0, out


def test_the_returned_id_actually_addresses_the_row(pg_like):
    """An id that is not the inserted row's is worse than an exception.

    set_meta and the confirm flow both address the row by this id, so a plausible
    wrong integer would corrupt someone else's entry rather than fail loudly.
    """
    out = sr.add_listed(pg_like, "glen@example.com", "Crystalline Clarity",
                        product_brand="Remedy Match")
    row = pg_like.execute(
        "SELECT email, product_name, status FROM supplement_reviews WHERE id=?",
        (out["id"],)).fetchone()
    assert row is not None, "the returned id matches no row"
    assert row[0] == "glen@example.com"
    assert row[1] == "Crystalline Clarity"
    assert row[2] == "listed"


def test_adding_the_same_product_twice_is_idempotent(pg_like):
    first = sr.add_listed(pg_like, "glen@example.com", "Crystalline Clarity",
                          product_brand="Remedy Match")
    second = sr.add_listed(pg_like, "glen@example.com", "Crystalline Clarity",
                           product_brand="Remedy Match")
    assert second["created"] is False
    assert second["id"] == first["id"]


def test_sqlite_still_gets_its_id():
    """The control. The Postgres branch must not be the only one that works."""
    cx = sqlite3.connect(":memory:")
    try:
        sr.init_table(cx)
        out = sr.add_listed(cx, "glen@example.com", "Crystalline Clarity",
                            product_brand="Remedy Match")
        assert out["created"] is True
        assert isinstance(out["id"], int) and out["id"] > 0
        row = cx.execute("SELECT product_name FROM supplement_reviews WHERE id=?",
                         (out["id"],)).fetchone()
        assert row[0] == "Crystalline Clarity"
    finally:
        cx.close()


def test_module_reads_no_lastrowid_anywhere():
    """Guards the file, not the two lines that broke.

    Walks the AST for any `.lastrowid` attribute read, so a third one cannot be
    added somewhere else in this module without failing here.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sr))
    offenders = [n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and n.attr == "lastrowid"]
    assert not offenders, f"lastrowid is back in supplement_reviews.py: {offenders}"
