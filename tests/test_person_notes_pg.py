"""Appending to people.notes, against SQLite AND a real Postgres.

POST /api/people/<id>/note returned 500 in production for every caller: the
statement used `char(10)`, a SQLite function that Postgres reads as the TYPE
character(10). No test covered the endpoint at all, so nothing said so, and the
number I wrote for a client on 2026-09-12 landed with no record of where it came
from.

A SQLite-only test would still pass with the old SQL, which is the whole point
of running this one against a real server too.
"""
import datetime
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager

import pytest

from dashboard.person_notes import append_note, note_line

NOW = datetime.datetime(2026, 9, 12, 8, 30, tzinfo=datetime.timezone.utc)


# ── the dated line ───────────────────────────────────────────────────────────

def test_the_line_carries_a_date_and_the_text():
    assert note_line("rang her", NOW) == "[2026-09-12 08:30] rang her"


# ── SQLite ───────────────────────────────────────────────────────────────────

class _Cx:
    """The app's connection shape: execute() returns something with fetchone()."""
    def __init__(self, conn): self._c = conn
    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("?", "?"), params)


@pytest.fixture
def lite():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, notes TEXT)")
    return conn


def _notes(conn, pid):
    return conn.execute("SELECT notes FROM people WHERE id=?", (pid,)).fetchone()[0]


def test_sqlite_writes_the_first_note_with_no_leading_blank_line(lite):
    lite.execute("INSERT INTO people (id, notes) VALUES (1, '')")
    append_note(_Cx(lite), 1, "first", NOW)
    assert _notes(lite, 1) == "[2026-09-12 08:30] first"


def test_sqlite_appends_the_second_on_its_own_line(lite):
    lite.execute("INSERT INTO people (id, notes) VALUES (1, '')")
    append_note(_Cx(lite), 1, "first", NOW)
    append_note(_Cx(lite), 1, "second", NOW)
    assert _notes(lite, 1).splitlines() == ["[2026-09-12 08:30] first",
                                            "[2026-09-12 08:30] second"]


def test_a_null_notes_column_is_written_not_wiped(lite):
    """The old CASE tested notes='', which is false for NULL, so a NULL row took
    the concat branch and NULL || anything is NULL. The first note would have
    silently erased the column."""
    lite.execute("INSERT INTO people (id, notes) VALUES (1, NULL)")
    append_note(_Cx(lite), 1, "first", NOW)
    assert _notes(lite, 1) == "[2026-09-12 08:30] first"


def test_an_existing_note_is_never_lost(lite):
    lite.execute("INSERT INTO people (id, notes) VALUES (1, 'intake form answers')")
    append_note(_Cx(lite), 1, "phone from her own email", NOW)
    assert _notes(lite, 1).startswith("intake form answers\n[")


# ── a real Postgres, which is where it actually broke ────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _server():
    initdb = shutil.which("initdb") or "/opt/homebrew/opt/postgresql@16/bin/initdb"
    pg_ctl = shutil.which("pg_ctl") or "/opt/homebrew/opt/postgresql@16/bin/pg_ctl"
    if not (os.path.exists(initdb) and os.path.exists(pg_ctl)):
        pytest.skip("no local Postgres binary")
    d = tempfile.mkdtemp()
    data, port = os.path.join(d, "data"), _free_port()
    subprocess.run([initdb, "-D", data, "-U", "postgres", "-A", "trust"],
                   check=True, capture_output=True)
    subprocess.run([pg_ctl, "-D", data, "-o", f"-p {port} -k {d}", "-l",
                    os.path.join(d, "log"), "start", "-w"],
                   check=True, capture_output=True)
    try:
        yield port, d
    finally:
        subprocess.run([pg_ctl, "-D", data, "stop", "-m", "immediate"],
                       capture_output=True)
        shutil.rmtree(d, ignore_errors=True)


class _PgCx:
    """Translates '?' to '%s' the way dashboard.db does on Postgres."""
    def __init__(self, conn): self._c = conn
    def execute(self, sql, params=()):
        cur = self._c.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur


def test_postgres_appends_rather_than_raising_a_syntax_error():
    psycopg2 = pytest.importorskip("psycopg2")
    import psycopg2.extras
    with _server() as (port, sock):
        conn = psycopg2.connect(host=sock, port=port, user="postgres",
                                dbname="postgres",
                                cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE people (id int primary key, notes text)")
            cur.execute("INSERT INTO people VALUES (1, NULL), (2, 'existing')")
        append_note(_PgCx(conn), 1, "first", NOW)
        append_note(_PgCx(conn), 1, "second", NOW)
        append_note(_PgCx(conn), 2, "added", NOW)
        with conn.cursor() as cur:
            cur.execute("SELECT id, notes FROM people ORDER BY id")
            got = {r["id"]: r["notes"] for r in cur.fetchall()}
        assert got[1].splitlines() == ["[2026-09-12 08:30] first",
                                       "[2026-09-12 08:30] second"]
        assert got[2] == "existing\n[2026-09-12 08:30] added"
