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
