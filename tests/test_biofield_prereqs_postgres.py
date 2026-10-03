"""Postgres only: a failed read must not abort the connection's transaction.

On Postgres one failed statement aborts the whole transaction, so an unguarded read
of a missing column made every later read on the connection fail, the portal's other
blocks included. Sqlite never aborts, so only this file can catch a lost savepoint.
Runs when BIOFIELD_PG_TEST_DSN names a throwaway database; skipped otherwise (CI).
"""
import os
from datetime import date

import pytest

DSN = os.environ.get("BIOFIELD_PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="BIOFIELD_PG_TEST_DSN not set")


def test_old_table_shape_reads_ready_and_leaves_the_connection_usable(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("PG_DSN", DSN)
    from dashboard import db, biofield_prereqs as bp
    cx = db.connect("unused")
    try:
        cx.execute("DROP TABLE IF EXISTS biofield_readiness")
        cx.execute("DROP TABLE IF EXISTS scan_freshness")
        cx.execute("CREATE TABLE biofield_readiness (email TEXT PRIMARY KEY, paid_at TEXT, "
                   "photo_on_file INTEGER NOT NULL DEFAULT 0, "
                   "intake_confirmed INTEGER NOT NULL DEFAULT 0, "
                   "scan_confirmed INTEGER NOT NULL DEFAULT 0)")
        cx.execute("INSERT INTO biofield_readiness (email, photo_on_file, intake_confirmed) "
                   "VALUES ('c@x.com', 1, 1)")
        cx.execute("CREATE TABLE scan_freshness (email TEXT PRIMARY KEY, last_scan_date TEXT, "
                   "updated_at TEXT)")
        cx.execute("INSERT INTO scan_freshness VALUES ('c@x.com', ?, '')",
                   (bp.business_today().isoformat(),))
        cx.commit()
        assert bp.status(cx, "c@x.com", today=bp.business_today())["ready"] is True
        assert cx.execute("SELECT 1").fetchone()[0] == 1
    finally:
        cx.rollback()
        cx.execute("DROP TABLE IF EXISTS biofield_readiness")
        cx.execute("DROP TABLE IF EXISTS scan_freshness")
        cx.commit()
        cx.close()
