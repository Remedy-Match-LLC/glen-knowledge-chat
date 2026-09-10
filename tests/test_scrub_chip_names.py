"""Clean up first names that are really our own chip labels.

The writer-side guard (begin_funnel.scrub_first_name) stops NEW damage, but
record_unlock falls back to the stored value when the scrub returns empty:

    new_first = scrub_first_name(first_name) or existing.get("first_name") or ""

so rows contaminated before the guard shipped keep being used. This cleans the
stored rows, in both journey_state and the people hub.

It reuses begin_funnel._chip_label_fragment rather than re-deriving "what is a
chip label", so the cleanup and the guard cannot drift apart.
"""
import importlib
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
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "log.db"), raising=False)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    appmod.app.config["TESTING"] = True
    from dashboard import db
    with db.connect(appmod.LOG_DB) as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT,
            first_name TEXT, last_name TEXT)""")
        cx.execute("""CREATE TABLE IF NOT EXISTS journey_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, email TEXT,
            first_name TEXT, last_name TEXT)""")
        for e, n in (("chip1@x.com", "Sharper Vision"), ("chip2@x.com", "On A"),
                     ("real@x.com", "Irene"), ("lower@x.com", "de la Cruz")):
            cx.execute("INSERT INTO people (email,name,first_name) VALUES (?,?,?)",
                       (e, n, n))
            cx.execute("INSERT INTO journey_state (session_id,email,first_name) "
                       "VALUES (?,?,?)", ("s-" + e, e, n))
        cx.commit()
    return appmod.app.test_client(), appmod


def _names(appmod, table):
    from dashboard import db
    with db.connect(appmod.LOG_DB) as cx:
        return dict(cx.execute(f"SELECT email, first_name FROM {table} "
                               "ORDER BY email").fetchall())


def test_dry_run_reports_without_changing_anything(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    before = _names(appmod, "people")
    r = c.post("/api/console/scrub-chip-names?dry_run=1")
    assert r.status_code == 200
    b = r.get_json()
    assert b["dry_run"] is True
    assert b["people"] == 2 and b["journey_state"] == 2
    assert {x["email"] for x in b["would_clear"]} == {"chip1@x.com", "chip2@x.com"}
    assert _names(appmod, "people") == before, "a dry run must write nothing"


def test_a_real_run_blanks_only_the_chip_labels(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    c.post("/api/console/scrub-chip-names")
    people = _names(appmod, "people")
    assert people["chip1@x.com"] == ""
    assert people["chip2@x.com"] == ""
    assert people["real@x.com"] == "Irene", "a real name must be untouched"
    assert people["lower@x.com"] == "de la Cruz", "a lowercase surname is a real name"


def test_it_cleans_journey_state_too(tmp_path, monkeypatch):
    """Blanking only the hub would be undone: record_unlock falls back to the
    stored journey_state value when the scrub returns empty."""
    c, appmod = _client(tmp_path, monkeypatch)
    c.post("/api/console/scrub-chip-names")
    js = _names(appmod, "journey_state")
    assert js["chip1@x.com"] == "" and js["chip2@x.com"] == ""
    assert js["real@x.com"] == "Irene"


def test_it_is_idempotent(tmp_path, monkeypatch):
    c, appmod = _client(tmp_path, monkeypatch)
    first = c.post("/api/console/scrub-chip-names").get_json()
    second = c.post("/api/console/scrub-chip-names").get_json()
    assert first["people"] == 2
    assert second["people"] == 0, "a second run has nothing left to do"


def test_it_uses_the_funnels_own_predicate(tmp_path, monkeypatch):
    """Set membership, not a heuristic. If this ever diverges from the guard,
    the cleanup could blank a real name or miss a chip label."""
    import begin_funnel
    assert begin_funnel._chip_label_fragment("Sharper Vision") is True
    assert begin_funnel._chip_label_fragment("Irene") is False
    assert begin_funnel._chip_label_fragment("de la Cruz") is False
