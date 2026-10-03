"""A wedged Postgres pool replaces itself instead of failing every request.

2026-10-02, 19:06-20:01 UTC: Postgres dropped the app's connections. The pool logged
reconnect failures until 19:14, then went silent and never logged giving up, while the
database itself was healthy from about 19:15. Every DB route failed with PoolTimeout
after 5 s for 55 minutes, until Glen restarted the service.

The heal fires only when all three hold: no connection is checked out by this process,
a direct connection to the database succeeds, and no heal ran in the last minute. A pool
that is merely busy, or a database that is really down, is left alone.
"""
import pytest
from psycopg_pool import PoolTimeout

from dashboard import db

DSN = "postgresql://example.invalid/test"


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_a, **_k):
        pass


class Raw:
    def cursor(self):
        return Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass


class WedgedPool:
    def __init__(self):
        self.closed = False

    def getconn(self, *, timeout):
        raise PoolTimeout(f"couldn't get a connection after {timeout:.2f} sec")

    def putconn(self, raw):
        pass

    def close(self, timeout=None):
        self.closed = True


class GoodPool:
    def __init__(self):
        self.out = 0

    def getconn(self, *, timeout):
        self.out += 1
        return Raw()

    def putconn(self, raw):
        self.out -= 1


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PG_DSN", DSN)
    monkeypatch.setattr(db, "_ensure_pg_schema", lambda *_a: None)
    monkeypatch.setattr(db, "_PG_POOLS", {})
    monkeypatch.setattr(db, "_PG_CHECKED_OUT", {})
    monkeypatch.setattr(db, "_PG_LAST_HEAL", {})
    made = []

    def new_pool(dsn, timeout):
        p = GoodPool()
        made.append(p)
        return p

    monkeypatch.setattr(db, "_new_pg_pool", new_pool)
    probes = []
    monkeypatch.setattr(db, "_pg_reachable", lambda dsn: probes.append(dsn) or True)
    return {"made": made, "probes": probes, "mp": monkeypatch}


def test_a_wedged_pool_is_replaced_and_the_request_succeeds(env):
    wedged = WedgedPool()
    db._PG_POOLS[DSN] = wedged
    cx = db._connect_postgres("chat_log.db", timeout=1)
    assert db._PG_POOLS[DSN] is env["made"][0]
    import time
    for _ in range(50):                       # closed off the request path, in a thread
        if wedged.closed:
            break
        time.sleep(0.01)
    assert wedged.closed
    cx.close()
    assert db._PG_CHECKED_OUT[DSN] == 0


def test_a_busy_pool_is_not_replaced(env):
    """Connections checked out means real load, not a wedge: the timeout stands."""
    wedged = WedgedPool()
    db._PG_POOLS[DSN] = wedged
    db._PG_CHECKED_OUT[DSN] = 3
    with pytest.raises(PoolTimeout):
        db._connect_postgres("chat_log.db", timeout=1)
    assert db._PG_POOLS[DSN] is wedged and not env["probes"]


def test_a_database_that_is_down_does_not_replace_the_pool(env):
    env["mp"].setattr(db, "_pg_reachable", lambda dsn: False)
    wedged = WedgedPool()
    db._PG_POOLS[DSN] = wedged
    with pytest.raises(PoolTimeout):
        db._connect_postgres("chat_log.db", timeout=1)
    assert db._PG_POOLS[DSN] is wedged and not wedged.closed


def test_at_most_one_heal_a_minute(env):
    db._PG_POOLS[DSN] = WedgedPool()
    db._connect_postgres("chat_log.db", timeout=1).close()
    db._PG_POOLS[DSN] = WedgedPool()          # wedges again at once
    with pytest.raises(PoolTimeout):
        db._connect_postgres("chat_log.db", timeout=1)
    assert len(env["made"]) == 1


def test_checked_out_count_follows_every_release_path(env):
    pool = GoodPool()
    db._PG_POOLS[DSN] = pool
    a = db._connect_postgres("chat_log.db", timeout=1)
    b = db._connect_postgres("chat_log.db", timeout=1)
    assert db._PG_CHECKED_OUT[DSN] == 2
    with a:
        pass
    b.close()
    b.close()                                 # a second release must not double count
    assert db._PG_CHECKED_OUT[DSN] == 0 and pool.out == 0
