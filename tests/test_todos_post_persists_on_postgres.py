"""POST /api/todos must actually insert, on Postgres as well as SQLite.

The bug this pins: the handler ran `SELECT changes()` after its INSERT to decide
whether to count the row. `changes()` is SQLite-only. On Postgres it raises
UndefinedFunction, a bare `except Exception: pass` swallowed it, and the poisoned
transaction dropped the INSERT. The endpoint returned `{"ok": true, "inserted": 0}`
with HTTP 201 the whole time.

Production has been on Postgres since the migration, and the console's newest todo
was 2026-07-22 when this was found on 2026-08-31: every todo pushed by
console_push_cron for five weeks was silently discarded.

A SQLite-only behavioural test cannot catch this, because `changes()` works there.
So there are two tests: one for behaviour, and one that pins the SQLite-only call
out of the file.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _client(tmp_path, monkeypatch):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:  # noqa: BLE001 — matches the other route tests
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "log.db"), raising=False)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "", raising=False)
    appmod._init_todos_table()   # build the schema with the app's own DDL
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def test_a_posted_todo_reports_itself_inserted(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.post("/api/todos", json={"owner": "glen", "title": "probe",
                                   "dedup_key": "t:probe:1"})
    assert r.status_code == 201
    assert r.get_json()["inserted"] == 1, (
        "a 201 with inserted=0 is a success badge on a no-op")


def test_a_posted_todo_is_readable_back(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    c.post("/api/todos", json={"owner": "glen", "title": "readable",
                               "dedup_key": "t:probe:2"})
    rows = c.get("/api/todos?owner=glen&status=open").get_json()["todos"]
    assert any(t["title"] == "readable" for t in rows)


def test_a_repeated_dedup_key_yields_one_row(tmp_path, monkeypatch):
    # The handler UPSERTs, so a repeat reports a change; what must hold is that
    # the console does not accumulate duplicates of the same alert.
    c, _ = _client(tmp_path, monkeypatch)
    for _ in range(2):
        c.post("/api/todos", json={"owner": "glen", "title": "once",
                                   "dedup_key": "t:probe:3"})
    rows = c.get("/api/todos?owner=glen&status=open").get_json()["todos"]
    assert len([t for t in rows if t["title"] == "once"]) == 1


def test_no_sqlite_only_changes_call_survives_in_app():
    """`SELECT changes()` is SQLite-only and raises on Postgres.

    Behaviour tests run on SQLite here, where changes() works, so only a source
    check can keep this from coming back. Use `cx.execute(...).rowcount`, which
    both backends implement (dashboard/db.py _PgCursor.rowcount).
    """
    src = (REPO / "app.py").read_text()
    hits = [m.start() for m in re.finditer(r"SELECT\s+changes\s*\(\s*\)", src, re.I)]
    lines = [src[:h].count("\n") + 1 for h in hits]
    assert hits == [], f"SQLite-only changes() at app.py lines {lines}"
