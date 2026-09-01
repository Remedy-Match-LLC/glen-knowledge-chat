"""Run the sequence DDL and upsert against a real Postgres.

New DDL and a new ON CONFLICT path both went to production today in a state that
SQLite accepted and Postgres refused, twice in one statement (see
tests/test_todos_upsert_on_real_postgres.py). New schema does not get to skip this
check.

The DDL is extracted from the module's own `init_tables` at test time, so the test
cannot drift from the code it is checking.

Skipped without TEST_PG_DSN, so the secretless CI run is unaffected:

    initdb -D /tmp/pgt/data -U postgres --auth=trust
    pg_ctl -D /tmp/pgt/data -o "-p 55433 -k /tmp/pgt" start
    createdb -h /tmp/pgt -p 55433 -U postgres seqtest
    TEST_PG_DSN="host=/tmp/pgt port=55433 user=postgres dbname=seqtest" pytest ...
"""
import inspect
import os
import re

import pytest

psycopg = pytest.importorskip("psycopg")

from dashboard import sequences  # noqa: E402
from dashboard.pgcompat import translate_sql  # noqa: E402

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

STEPS = [{"step_no": 1, "subject": "s1", "body_md": "b1", "delay_days": 0},
         {"step_no": 2, "subject": "s2", "body_md": "b2", "delay_days": 4}]


class _Shim:
    """Minimal stand-in for dashboard.db's connection: translate, then execute."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, params=()):
        return self._c.execute(translate_sql(sql), tuple(params))

    def commit(self):
        pass


@pytest.fixture()
def cx():
    conn = psycopg.connect(DSN, autocommit=True)
    for t in ("sequence_sends", "sequence_enrollments", "sequence_steps", "sequences"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    shim = _Shim(conn)
    sequences.init_tables(shim)
    yield shim
    conn.close()


def test_every_ddl_statement_is_valid_postgres(cx):
    src = inspect.getsource(sequences.init_tables)
    stmts = re.findall(r'cx\.execute\("""(.*?)"""\)', src, re.S)
    assert len(stmts) == 4, "init_tables should create four tables"
    # The fixture already ran them; re-running proves idempotency on PG too.
    sequences.init_tables(cx)


def test_upsert_and_read_back_on_postgres(cx):
    sequences.upsert(cx, slug="nurture", name="Nurture",
                     trigger_kind="on_contact_created", active=False, steps=STEPS)
    got = sequences.get(cx, "nurture")
    assert got["name"] == "Nurture"
    assert [s["step_no"] for s in got["steps"]] == [1, 2]


def test_the_on_conflict_branch_runs_on_postgres(cx):
    # This is the shape that raised AmbiguousColumn in the todos handler.
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     active=False, steps=STEPS)
    sequences.upsert(cx, slug="nurture", name="Nurture v2", trigger_kind="manual",
                     active=True, steps=STEPS)
    got = sequences.get(cx, "nurture")
    assert got["name"] == "Nurture v2"
    assert len(got["steps"]) == 2, "a re-push must not duplicate steps"
    # INVERTED 2026-09-01: this used to assert active became True. `active` is now
    # set on INSERT only and never by ON CONFLICT, so a copy push cannot start a
    # drip sending. Kept rather than deleted so the guard is pinned on Postgres too.
    assert got["active"] is False, "a push must not activate a sequence"


def test_set_active_is_the_only_way_to_go_live_on_postgres(cx):
    sequences.upsert(cx, slug="nurture", name="Nurture", trigger_kind="manual",
                     steps=STEPS)
    sequences.set_active(cx, "nurture", True)
    assert sequences.get(cx, "nurture")["active"] is True
    # And a later copy push must not switch it back off.
    sequences.upsert(cx, slug="nurture", name="Renamed", trigger_kind="manual",
                     steps=STEPS)
    assert sequences.get(cx, "nurture")["active"] is True


def test_removing_a_step_removes_it_on_postgres(cx):
    sequences.upsert(cx, slug="n", name="N", trigger_kind="manual", steps=STEPS)
    sequences.upsert(cx, slug="n", name="N", trigger_kind="manual", steps=STEPS[:1])
    assert [s["step_no"] for s in sequences.get(cx, "n")["steps"]] == [1]


def test_list_all_counts_steps_on_postgres(cx):
    sequences.upsert(cx, slug="a", name="A", trigger_kind="manual", steps=STEPS)
    rows = {r["slug"]: r["step_count"] for r in sequences.list_all(cx)}
    assert rows == {"a": 2}
