"""Postgres only: the launch email's reservation lookup on a table without founding columns.

Production, 2026-11-12: subscriptions exists but the founding columns do not, so the
lookup raised UndefinedColumn and no launch email could be sent. On Postgres that
failed SELECT also aborts the transaction. This file checks the savepoint: the lookup
returns no reservers, the connection stays usable, and the caller's earlier
uncommitted write survives to be committed or rolled back. Sqlite never aborts, so
only this file can catch a lost savepoint.
Runs when BIOFIELD_PG_TEST_DSN names a throwaway database; skipped otherwise (CI).
"""
import os

import pytest

DSN = os.environ.get("BIOFIELD_PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="BIOFIELD_PG_TEST_DSN not set")


def _fresh(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("PG_DSN", DSN)
    from dashboard import db
    cx = db.connect("unused")
    cx.execute("DROP TABLE IF EXISTS subscriptions")
    cx.execute("DROP TABLE IF EXISTS wl_probe")
    cx.execute("CREATE TABLE subscriptions (id SERIAL PRIMARY KEY, email TEXT, status TEXT)")
    cx.execute("CREATE TABLE wl_probe (v TEXT)")
    cx.commit()
    return cx


def _cleanup(cx):
    cx.rollback()
    cx.execute("DROP TABLE IF EXISTS subscriptions")
    cx.execute("DROP TABLE IF EXISTS wl_probe")
    cx.commit()
    cx.close()


def test_missing_columns_mean_no_reservers_and_keep_the_earlier_write(monkeypatch):
    from dashboard import product_waitlist as pw
    cx = _fresh(monkeypatch)
    try:
        cx.execute("INSERT INTO wl_probe VALUES ('kept')")       # uncommitted
        assert pw._reserved(cx, "neuro-magnesium") == set()
        assert cx.execute("SELECT 1").fetchone()[0] == 1          # not aborted
        cx.commit()
        assert cx.execute("SELECT v FROM wl_probe").fetchone()[0] == "kept"
    finally:
        _cleanup(cx)


def test_the_caller_can_still_roll_its_write_back(monkeypatch):
    from dashboard import product_waitlist as pw
    cx = _fresh(monkeypatch)
    try:
        cx.execute("INSERT INTO wl_probe VALUES ('dropped')")
        assert pw._reserved(cx, "neuro-magnesium") == set()
        cx.rollback()
        assert cx.execute("SELECT COUNT(*) FROM wl_probe").fetchone()[0] == 0
    finally:
        _cleanup(cx)


def test_reservations_are_still_found_once_the_columns_exist(monkeypatch):
    from dashboard import product_waitlist as pw
    from dashboard import subscriptions as subs
    cx = _fresh(monkeypatch)
    try:
        subs.migrate_add_founding_columns(cx)
        cx.execute("INSERT INTO subscriptions (email, status, founding, founding_slug) "
                   "VALUES ('res@x.com', 'active', 1, 'neuro-magnesium')")
        cx.commit()
        monkeypatch.setattr(pw, "_person_email", lambda _cx, e: e)
        assert pw._reserved(cx, "neuro-magnesium") == {"res@x.com"}
    finally:
        _cleanup(cx)
