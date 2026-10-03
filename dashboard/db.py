"""Single DB access point so the backend (sqlite | postgres) is swappable.
Default is local SQLite — identical behavior to calling sqlite3.connect directly."""
import os
import sqlite3

from dashboard.pgcompat import translate_sql, HybridRow

# Backend-neutral exception types for `except db.X:` at sites that must tolerate the
# same DB error on either backend. Each is a strict SUPERSET of the sqlite3 type (it
# always includes it), so swapping `except sqlite3.X` -> `except db.X` is behavior-
# preserving on SQLite and additionally catches the Postgres equivalent — e.g. a UNIQUE
# violation is sqlite3.IntegrityError on SQLite and psycopg.IntegrityError (UniqueViolation)
# on Postgres. psycopg is imported guarded so a sqlite-only env without it (e.g. secretless
# CI) still works, falling back to the bare sqlite3 types.
try:
    import psycopg as _psycopg
    Error = (sqlite3.Error, _psycopg.Error)
    IntegrityError = (sqlite3.IntegrityError, _psycopg.IntegrityError)
    # sqlite3.OperationalError covers "no such table/column" + operational/lock issues;
    # on Postgres those are UndefinedTable/UndefinedColumn (ProgrammingError subclasses)
    # + OperationalError. Kept NARROW (schema-existence + operational) so a genuine dialect
    # bug (SyntaxError etc.) still surfaces instead of being silently swallowed.
    OperationalError = (sqlite3.OperationalError, _psycopg.OperationalError,
                        _psycopg.errors.UndefinedTable, _psycopg.errors.UndefinedColumn,
                        _psycopg.errors.DuplicateTable, _psycopg.errors.DuplicateColumn)
except ImportError:
    Error = sqlite3.Error
    IntegrityError = sqlite3.IntegrityError
    OperationalError = sqlite3.OperationalError

def backend() -> str:
    return (os.environ.get("DB_BACKEND") or "sqlite").strip().lower()

def backend_of(cx) -> str:
    """The backend a given connection object belongs to: a _PgConn is 'postgres';
    a plain sqlite3.Connection (or anything without a .backend tag) is 'sqlite'."""
    return getattr(cx, "backend", "sqlite")

def column_exists(cx, table: str, column: str) -> bool:
    """True if `table` has a column named `column`, on either backend.
    `table`/`column` come from code literals (schema-migration checks), not user input."""
    if backend_of(cx) == "postgres":
        row = cx.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=? AND column_name=? LIMIT 1",
            (table, column)).fetchone()
        return row is not None
    cols = [r[1] for r in cx.execute("PRAGMA table_info(%s)" % table).fetchall()]
    return column in cols

def connect(db_path: str, *, timeout: float = 5.0):
    b = backend()
    if b == "sqlite":
        return sqlite3.connect(db_path, timeout=timeout)
    if b == "postgres":
        return _connect_postgres(db_path, timeout=timeout)
    raise ValueError("unknown DB_BACKEND: %r" % b)

class _PgCursor:
    def __init__(self, cur):
        self._cur = cur
    def execute(self, sql, params=()):
        self._cur.execute(translate_sql(sql), tuple(params))
        return self
    @property
    def rowcount(self):
        # sqlite3.Cursor.rowcount parity: rows affected by the last DML.
        return self._cur.rowcount
    @property
    def description(self):
        # sqlite3.Cursor.description parity: one 7-tuple per column, name first,
        # or None when the statement has no result set (CREATE, a plain INSERT).
        # An empty SELECT still describes its columns, as in SQLite. Without it,
        # triage.resolve_invite raised on Postgres for every triage invite token,
        # and evox.get_readiness did the same until #1895.
        desc = self._cur.description
        if desc is None:
            return None
        return tuple((d.name, None, None, None, None, None, None) for d in desc)
    @property
    def lastrowid(self):
        # psycopg has no lastrowid; an INSERT that needs its new id must use
        # `INSERT ... RETURNING id` on Postgres. Fail loud (per-site fix during
        # runtime porting) rather than silently returning None, which callers
        # would mistake for a valid row id.
        raise AttributeError(
            "lastrowid is unavailable on the Postgres backend; "
            "use 'INSERT ... RETURNING id' and read fetchone()[0]")
    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d.name for d in self._cur.description]
        return HybridRow(cols, row)
    def fetchall(self):
        rows = self._cur.fetchall()
        cols = [d.name for d in self._cur.description]
        return [HybridRow(cols, r) for r in rows]
    def __iter__(self):
        # Match sqlite3.Cursor: `for row in cx.execute(...)` yields rows directly.
        desc = self._cur.description
        if desc is None:
            return
        cols = [d.name for d in desc]
        for row in self._cur:
            yield HybridRow(cols, row)

class _PgConn:
    backend = "postgres"
    def __init__(self, conn, pool, on_release=None):
        self._conn = conn
        self._pool = pool
        self._released = False
        self._on_release = on_release   # keeps _PG_CHECKED_OUT; see _connect_postgres
    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        return _PgCursor(cur).execute(sql, params)
    def cursor(self):
        """sqlite3.Connection.cursor() parity.

        `cur = cx.cursor(); cur.execute(...).fetchall()` is ordinary sqlite3, and
        26 call sites across seven dashboard modules are written that way --
        topic_pages, mentor_pages, ingredient_pages, product_reviews,
        review_gifts, biofield_reveals. Without this they raise
        `AttributeError: '_PgConn' object has no attribute 'cursor'` on Postgres,
        which took /learn, /learn/sitemap.xml, /learn/<slug> and /mentors to 500
        in production: the SEO hub and its sitemap, unreachable and uncrawlable.

        Fixed here rather than at the call sites so every current and future
        caller of the idiom is covered by one change. Callers that follow it with
        `cur.row_factory = sqlite3.Row` are fine: _PgCursor has no __slots__ so
        the assignment is a harmless no-op, and HybridRow already supports the
        string-key access that row_factory exists to provide."""
        return _PgCursor(self._conn.cursor())
    def executescript(self, script):
        # sqlite3.Connection.executescript runs a whole ';'-separated DDL script
        # in one call; Postgres' extended protocol is one-command-per-execute, so
        # split (quote/comment-aware) and run each statement through the normal
        # translate path. Callers are all idempotent CREATE TABLE/INDEX init DDL.
        from dashboard.pgcompat import split_statements
        cur = self._conn.cursor()
        for stmt in split_statements(script):
            _PgCursor(cur).execute(stmt)
        return self
    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(translate_sql(sql), [tuple(p) for p in seq_of_params])
        return _PgCursor(cur)
    def commit(self):
        self._conn.commit()
    def rollback(self):
        self._conn.rollback()
    def _release(self):
        if not self._released:
            self._released = True
            try:
                self._pool.putconn(self._conn)
            finally:
                if self._on_release:
                    self._on_release()
    def close(self):
        self._release()
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            # Return it even when commit fails on a dead connection (round 3 of the
            # pool review): otherwise the checked-out count stays up and blocks healing.
            self._release()   # pooled resource: return on context exit
        return False
    def __del__(self):
        try:
            self._release()
        except Exception:
            pass

_PG_POOLS = {}          # dsn -> ConnectionPool
_PG_ENSURED = set()     # (dsn, schema) already CREATE SCHEMA'd
_PG_CHECKED_OUT = {}    # dsn -> connections this process holds right now
_PG_LAST_HEAL = {}      # dsn -> monotonic time of the last heal ATTEMPT (success or not)
_PG_PROBING = set()     # dsns with a heal attempt in flight: one probe at a time
_PG_HEAL_EVERY = 60.0   # seconds between heal attempts per pool
import threading as _threading
_PG_LOCK = _threading.Lock()
# The count has its own reentrant lock: a _PgConn released by the garbage collector
# (__del__) can run while this same thread holds a lock, and must not wait on itself.
_PG_COUNT_LOCK = _threading.RLock()


def _count_checked_out(dsn, delta):
    with _PG_COUNT_LOCK:
        _PG_CHECKED_OUT[dsn] = max(0, _PG_CHECKED_OUT.get(dsn, 0) + delta)
        return _PG_CHECKED_OUT[dsn]

def _get_pg_pool(dsn, timeout):
    with _PG_LOCK:
        pool = _PG_POOLS.get(dsn)
        if pool is None:
            pool = _new_pg_pool(dsn, timeout)
            _PG_POOLS[dsn] = pool
        return pool

def _new_pg_pool(dsn, timeout):
    from psycopg_pool import ConnectionPool
    # check= validates a pooled connection BEFORE handing it out and
    # discards a dead one. Without it (the default is None) psycopg_pool
    # never tests a connection on checkout, so one that Render's Postgres
    # has closed while idle sits in the pool and is served to the next
    # request, whose first I/O dies with
    #   consuming input failed: SSL error: unexpected eof while reading
    # 43 of those in 31 hours of production logs, each one a 500 to
    # whoever made that request. max_lifetime (3600s) and max_idle (600s)
    # already default sensibly and are deliberately left alone.
    # TCP keepalives (round 1 of the pool review): a request blocked reading a socket the
    # server has already dropped never returns on its own, and while it holds its
    # connection the pool cannot be judged idle, so _heal_pg_pool never fires. With
    # these the kernel declares such a socket dead after about 60 s (30 + 3 x 10).
    return ConnectionPool(dsn, min_size=2, max_size=10, open=True,
                          check=ConnectionPool.check_connection,
                          kwargs=_PG_CONNECT_KWARGS(timeout))


def _PG_CONNECT_KWARGS(timeout):
    return {"connect_timeout": max(1, int(round(timeout))),
            "keepalives": 1, "keepalives_idle": 30,
            "keepalives_interval": 10, "keepalives_count": 3}


def _pg_reachable(dsn):
    """One direct connection, outside the pool. True if the database answers."""
    try:
        import psycopg
        c = psycopg.connect(dsn, connect_timeout=3, autocommit=True,
                            options="-c statement_timeout=3000")
        try:
            c.execute("SELECT 1")
        finally:
            c.close()
        return True
    except Exception:
        return False


def _heal_pg_pool(dsn, pool):
    """Replace a wedged pool. True if a usable pool is now registered for `dsn`.

    2026-10-02, 19:06-20:01 UTC: Postgres dropped the connections, the pool's reconnect
    worker went silent after 19:14 without ever giving up, and every DB route failed with
    PoolTimeout for 55 minutes while the database was healthy. Only a restart fixed it.

    Replace only when all hold: this process has no connection checked out (so the
    timeout is not real load), the database answers a direct connection (so the pool,
    not the database, is at fault), and no replacement ran in the last minute."""
    import time
    from psycopg_pool import PoolTimeout
    with _PG_LOCK:
        if _PG_POOLS.get(dsn) is not pool:
            return dsn in _PG_POOLS          # another request already replaced it
        if dsn in _PG_PROBING or _PG_CHECKED_OUT.get(dsn, 0) > 0:
            return False
        if time.monotonic() - _PG_LAST_HEAL.get(dsn, -1e9) < _PG_HEAL_EVERY:
            return False
        _PG_PROBING.add(dsn)                 # rate-limit attempts, not only successes
        _PG_LAST_HEAL[dsn] = time.monotonic()
    try:
        if not _pg_reachable(dsn):
            return False
        # Second chance: a pool that was merely busy has drained by now and hands one out.
        try:
            pool.putconn(pool.getconn(timeout=0.5))
            return True
        except PoolTimeout:
            pass
        with _PG_LOCK:
            if _PG_POOLS.get(dsn) is not pool or _PG_CHECKED_OUT.get(dsn, 0) > 0:
                return _PG_POOLS.get(dsn) is not pool and dsn in _PG_POOLS
            del _PG_POOLS[dsn]
    finally:
        with _PG_LOCK:
            _PG_PROBING.discard(dsn)
    print("[db] Postgres pool wedged: none checked out and the database answers; "
          "replacing the pool", flush=True)
    # Close the old pool off the request path: its stuck workers can take seconds to stop.
    def _close_old():
        try:
            pool.close(timeout=1.0)
        except Exception as e:
            print(f"[db] closing the wedged pool failed (ignored): {type(e).__name__}", flush=True)
    _threading.Thread(target=_close_old, name="pg-pool-close", daemon=True).start()
    return True

def _ensure_pg_schema(raw, dsn, schema):
    key = (dsn, schema)
    with _PG_LOCK:
        if key in _PG_ENSURED:
            return
    with raw.cursor() as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    raw.commit()
    with _PG_LOCK:
        _PG_ENSURED.add(key)

def _connect_postgres(db_path: str, *, timeout: float):
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        raise RuntimeError("DB_BACKEND=postgres but PG_DSN is unset")
    from dashboard.dbschema import schema_for_path
    schema = schema_for_path(db_path)  # already sanitized to [a-z0-9_] -> safe to quote-interpolate
    pool = _get_pg_pool(dsn, timeout)
    # Pool exhaustion used to ignore ``connect(..., timeout=...)`` entirely:
    # psycopg_pool's default checkout wait is much longer than SQLite's bounded
    # busy timeout, leaving portal requests spinning behind the global loading
    # screen. Keep both backends on the same fail-fast contract.
    from psycopg_pool import PoolClosed, PoolTimeout
    try:
        raw = pool.getconn(timeout=max(0.1, float(timeout)))
    except (PoolTimeout, PoolClosed) as e:
        # PoolClosed: another request replaced this pool while we waited on it.
        if isinstance(e, PoolTimeout) and not _heal_pg_pool(dsn, pool):
            raise
        pool = _get_pg_pool(dsn, timeout)
        raw = pool.getconn(timeout=max(0.1, float(timeout)))
    _count_checked_out(dsn, +1)
    pc = None
    try:
        _ensure_pg_schema(raw, dsn, schema)
        with raw.cursor() as c:
            c.execute(f'SET search_path TO "{schema}"')
            # A checked-out connection can also block on a statement or row/table
            # lock after the pool wait succeeds. Bound both waits to the caller's
            # requested timeout; these are session settings and are refreshed on
            # every checkout, so a pooled connection cannot retain stale values.
            timeout_ms = max(100, int(float(timeout) * 1000))
            timeout_value = f"{timeout_ms}ms"
            c.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (timeout_value,),
            )
            c.execute(
                "SELECT set_config('lock_timeout', %s, false)",
                (timeout_value,),
            )
        raw.commit()
        pc = _PgConn(raw, pool, on_release=lambda: _count_checked_out(dsn, -1))
    except BaseException:
        # BaseException: a gevent Timeout or GreenletExit mid-setup must not strand the
        # connection or the count, or the pool can never be judged idle again.
        if pc is None:
            try:
                pool.putconn(raw)
            finally:
                _count_checked_out(dsn, -1)
        raise
    return pc
