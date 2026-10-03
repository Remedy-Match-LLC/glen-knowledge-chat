# tests/test_pg_cursor_description.py
"""`cursor.description` must work on both backends.

Seven modules read it the sqlite3 way, `[d[0] for d in cur.description]`.
`_PgCursor` had no such attribute, so on Postgres (prod) any caller that did
not first check `hasattr(row, "keys")` raised AttributeError.
`triage.resolve_invite` reads it before fetching, so every triage invite
lookup raised. Reproduced against a local Postgres:

    AttributeError: '_PgCursor' object has no attribute 'description'

`evox.get_readiness` hit the same thing (55 raises on 2026-09-29, #1895).
"""

import os
import sqlite3
from types import SimpleNamespace

import pytest

from dashboard import db


class _FakePsycopgCursor:
    def __init__(self, names, rows):
        self.description = [SimpleNamespace(name=n) for n in names] if names else None
        self._rows = list(rows)

    def execute(self, sql, params=()):
        pass

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


def test_description_matches_sqlite_shape():
    sq = sqlite3.connect(":memory:")
    want = sq.execute("SELECT 1 AS a, 2 AS b").description
    got = db._PgCursor(_FakePsycopgCursor(["a", "b"], [(1, 2)])).description
    assert got == want


def test_description_is_none_without_a_result_set():
    sq = sqlite3.connect(":memory:")
    sq.execute("CREATE TABLE t (x)")
    assert sq.execute("INSERT INTO t VALUES (1)").description is None
    assert db._PgCursor(_FakePsycopgCursor(None, [])).description is None


def test_the_sqlite_idiom_reads_names_before_fetching():
    """The exact shape triage.resolve_invite uses."""
    cur = db._PgCursor(_FakePsycopgCursor(["email", "status"], [("a@x", "invited")]))
    cols = [c[0] for c in cur.description]
    assert dict(zip(cols, cur.fetchone())) == {"email": "a@x", "status": "invited"}


@pytest.mark.skipif(not os.environ.get("TEST_PG_DSN"), reason="TEST_PG_DSN not set")
def test_triage_invite_resolves_on_real_postgres(monkeypatch):
    monkeypatch.setenv("PG_DSN", os.environ["TEST_PG_DSN"])
    monkeypatch.setenv("DB_BACKEND", "postgres")
    from dashboard import triage
    cx = db.connect("chat_log.db")
    try:
        triage.init_triage_tables(cx)
        cx.execute("DELETE FROM triage_invites WHERE email='desc-test@example.com'")
        cx.commit()
        token = triage.create_invite(cx, "desc-test@example.com", "Desc Test", "rae")
        got = triage.resolve_invite(cx, token)
        assert got and got["email"] == "desc-test@example.com"
        assert got["practitioner"] == "rae"
    finally:
        cx.execute("DELETE FROM triage_invites WHERE email='desc-test@example.com'")
        cx.commit()
        cx.close()
