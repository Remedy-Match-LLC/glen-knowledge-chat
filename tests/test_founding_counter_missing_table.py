"""Regression: the public founding counter must not 500 on a database that has
never had a subscription written (the `subscriptions` table is created lazily on
first write). count_founding treats a missing table as 0 reservations, so the
status route and the reserve open-check stay alive on a fresh prod DB."""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

import dashboard.subscriptions as subs


def test_count_founding_returns_zero_when_table_missing():
    cx = sqlite3.connect(":memory:")  # no tables at all
    assert subs.count_founding(cx, "neuro-magnesium") == 0


def test_count_founding_counts_when_table_present():
    cx = sqlite3.connect(":memory:")
    subs.init_subscriptions_table(cx)
    subs.migrate_add_founding_columns(cx)
    assert subs.count_founding(cx, "neuro-magnesium") == 0  # table exists, no rows


def _load_app():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


def test_founding_status_route_alive_without_subscriptions_table(monkeypatch, tmp_path):
    app_module = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app_module, "LOG_DB", db)
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)  # journey only, NO subscriptions table
    c = app_module.app.test_client()
    r = c.get("/begin/founding/status/neuro-magnesium")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    body = r.get_json()
    assert body["cap"] == 2500
    assert body["remaining"] == 2500   # 0 reserved -> full cap
    assert body["open"] is True


class _PgLike:
    """Behaves like a pooled Postgres connection: after a failed statement every later
    one fails until a rollback to a savepoint taken before it. The founding columns are
    missing, as on production before the first reservation (2026-10-14: the status route
    500ed on both domains)."""

    backend = "postgres"                 # as dashboard.db._PgConn

    def __init__(self, error=None, dead=False):
        self.dead = dead
        self.aborted = False
        self.savepoints = 0
        self.error = error

    def execute(self, sql, params=()):
        import psycopg
        s = sql.strip().upper()
        if s.startswith("ROLLBACK TO SAVEPOINT"):
            if self.dead:
                import psycopg
                raise psycopg.OperationalError("the connection is closed")
            self.aborted = False
            return self
        if self.aborted:
            raise psycopg.errors.InFailedSqlTransaction("current transaction is aborted")
        if s.startswith("SAVEPOINT"):
            self.savepoints += 1
            return self
        if s.startswith("RELEASE"):
            return self
        self.aborted = True
        raise self.error or psycopg.errors.UndefinedColumn('column "founding" does not exist')

    def rollback(self):
        raise AssertionError("a full rollback would discard the caller's writes")


def test_the_counter_survives_two_counts_on_a_postgres_like_connection():
    from dashboard import founding
    cx = _PgLike()
    # The route counts twice: is_open() calls remaining(), then remaining() again.
    assert founding.is_open(cx, "neuro-magnesium", now_iso="2026-10-14") is True
    assert founding.remaining(cx, "neuro-magnesium") == 2500
    assert cx.savepoints == 2 and not cx.aborted


def test_any_other_database_failure_is_raised_not_read_as_zero():
    import psycopg
    cx = _PgLike(error=psycopg.OperationalError("server closed the connection"))
    with pytest.raises(psycopg.OperationalError):
        subs.count_founding(cx, "neuro-magnesium")
    assert not cx.aborted


def test_a_real_reservation_is_counted():
    cx = sqlite3.connect(":memory:")
    subs.init_subscriptions_table(cx)
    subs.migrate_add_founding_columns(cx)
    subs.create_founding_reservation(cx, email="a@example.com", stripe_customer_id="cus",
                                     stripe_payment_method_id="pm", items=[], ship_address={},
                                     founding_slug="neuro-magnesium")
    assert subs.count_founding(cx, "neuro-magnesium") == 1


def test_a_missing_column_keeps_the_callers_unsaved_write():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, status TEXT)")
    cx.execute("CREATE TABLE other (x TEXT)")
    cx.commit()
    cx.execute("INSERT INTO other VALUES ('kept')")          # not committed yet
    assert subs.count_founding(cx, "neuro-magnesium") == 0   # founding column missing
    assert cx.execute("SELECT x FROM other").fetchall() == [("kept",)]


def test_on_a_dead_connection_the_counts_own_error_is_raised():
    import psycopg
    cx = _PgLike(dead=True)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        subs.count_founding(cx, "neuro-magnesium")
