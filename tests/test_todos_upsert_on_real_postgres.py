"""Run the todos upsert against a real Postgres, because SQLite accepts what
Postgres rejects.

Two separate incompatibilities lived in this one statement and neither was
reachable from a SQLite-backed test:

  1. `SELECT changes()` after the INSERT — SQLite-only, raises on Postgres.
  2. `ELSE received_at END` inside DO UPDATE — Postgres raises AmbiguousColumn
     because an unqualified name there could mean the target row or `excluded`.

Both were swallowed by a bare `except: pass`, so POST /api/todos returned HTTP 201
with `inserted: 0` and dropped five weeks of console_push_cron output.

Skips when no Postgres is reachable, so the secretless CI run is unaffected. Point
it at one with TEST_PG_DSN, e.g.

    initdb -D /tmp/pgt/data -U postgres --auth=trust
    pg_ctl -D /tmp/pgt/data -o "-p 55432 -k /tmp/pgt" start
    createdb -h /tmp/pgt -p 55432 -U postgres todotest
    TEST_PG_DSN="host=/tmp/pgt port=55432 user=postgres dbname=todotest" pytest ...
"""
import os
import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_PG_DSN")
REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")


def _app_sql(marker, end_marker='"""'):
    """Pull the real statement out of app.py so the test cannot drift from it."""
    src = (REPO / "app.py").read_text()
    i = src.index(marker)
    j = src.index(end_marker, i)
    return src[i:j]


@pytest.fixture()
def cx():
    from dashboard.pgcompat import translate_sql
    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS todos")
    ddl = _app_sql("CREATE TABLE IF NOT EXISTS todos")
    conn.execute(translate_sql(ddl))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_todos_dedup "
                 "ON todos(dedup_key)")
    yield conn
    conn.close()


def _upsert(cx, dedup, title, received_at=""):
    from dashboard.pgcompat import translate_sql
    sql = _app_sql("\n                    INSERT INTO todos\n"
                   "                      (created_at, owner, category")
    params = ("2026-08-31T00:00:00Z", "glen", "General", title, "b", "normal",
              "test", dedup, "", "", "", "", received_at)
    cur = cx.execute(translate_sql(sql), params)
    return cur.rowcount


def test_the_real_insert_statement_runs_on_postgres(cx):
    assert _upsert(cx, "d:1", "first") == 1
    row = cx.execute("SELECT title FROM todos WHERE dedup_key='d:1'").fetchone()
    assert row[0] == "first"


def test_the_upsert_branch_runs_on_postgres(cx):
    # This is the path that raised AmbiguousColumn in production.
    _upsert(cx, "d:2", "first", received_at="2026-01-01")
    _upsert(cx, "d:2", "second", received_at="")
    rows = cx.execute("SELECT received_at FROM todos WHERE dedup_key='d:2'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-01-01", (
        "a blank received_at must preserve the stored value, not overwrite it")


def test_a_nonblank_received_at_overwrites(cx):
    _upsert(cx, "d:3", "first", received_at="2026-01-01")
    _upsert(cx, "d:3", "second", received_at="2026-02-02")
    row = cx.execute("SELECT received_at FROM todos WHERE dedup_key='d:3'").fetchone()
    assert row[0] == "2026-02-02"


def test_no_unqualified_column_in_the_do_update_branch():
    """Pins the fix even when no Postgres is available.

    An unqualified name on the right of DO UPDATE SET is ambiguous in Postgres.
    Qualify it as todos.<col>.
    """
    src = (REPO / "app.py").read_text()
    bad = []
    # Only DO UPDATE SET blocks matter: that is where `excluded` is in scope and a
    # bare name becomes ambiguous. A plain UPDATE or an ORDER BY CASE is fine.
    for m in re.finditer(r"DO\s+UPDATE\s+SET", src, re.I):
        end = src.find('"""', m.end())
        block = src[m.end():end if end != -1 else m.end() + 2000]
        for name in re.findall(r"ELSE\s+(?!todos\.|excluded\.)([a-z_]\w*)\s+END",
                               block, re.I):
            line = src[:m.end()].count("\n") + 1
            bad.append(f"{name} (near app.py:{line})")
    assert bad == [], f"unqualified column(s) in a DO UPDATE branch: {bad}"
