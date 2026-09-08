"""The monthly triage sweep, running on production rather than Glen's Mac.

The three `category:` Gmail filters archived mail the instant it arrived, with
no age guard and no starred guard, because a filter runs at delivery when every
message is zero days old. They were removed on Glen's decision. This sweep
replaces them and keeps both guards, because it runs against mail that has been
sitting for 30 days.

Automating a bulk archive needs its own guards:
  - dry run by DEFAULT, so a mis-call reports instead of archiving
  - a cap, so a query that suddenly matches everything refuses instead of
    emptying the inbox
  - one definition of the query, shared with the CLI, so the two cannot drift
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import app, dashboard
    from dashboard import inbox as _inbox
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)


def _auth(mp):
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)


def _fake_service(ids, archived):
    """Stands in for Gmail. Records every batchModify it is asked to perform."""
    class _Exec:
        def __init__(self, v): self.v = v
        def execute(self): return self.v
    class _Messages:
        def list(self, userId=None, q=None, maxResults=None, pageToken=None):
            return _Exec({"messages": [{"id": i} for i in ids]})
        def batchModify(self, userId=None, body=None):
            archived.append(body)
            return _Exec("")
    class _Users:
        def messages(self): return _Messages()
    class _Svc:
        def users(self): return _Users()
    return _Svc()


# ── The query keeps both guards, and there is only one of it ─────────────────

def test_sweep_query_keeps_the_age_and_starred_guards():
    q = _inbox.TRIAGE_ARCHIVE_QUERY
    assert "-is:starred" in q, q
    assert "older_than:30d" in q, q
    assert "in:inbox" in q, q
    for cat in ("promotions", "updates", "social"):
        assert f"category:{cat}" in q, q


def test_the_cli_uses_the_same_query_object():
    """Two definitions of this query would drift, and the drift would be silent."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "inbox_triage", Path(__file__).resolve().parent.parent / "scripts" / "inbox_triage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ARCHIVE_QUERY == _inbox.TRIAGE_ARCHIVE_QUERY


# ── Dry run is the default ───────────────────────────────────────────────────

def test_sweep_defaults_to_dry_run_and_archives_nothing(monkeypatch):
    archived = []
    monkeypatch.setattr(_inbox, "_get_gmail_service",
                        lambda: _fake_service(["m1", "m2", "m3"], archived))

    report = _inbox.triage_sweep()

    assert archived == [], f"a default call archived: {archived}"
    assert report["dry_run"] is True, report
    assert report["matched"] == 3, report
    assert report["archived"] == 0, report


def test_sweep_archives_only_when_told_to(monkeypatch):
    archived = []
    monkeypatch.setattr(_inbox, "_get_gmail_service",
                        lambda: _fake_service(["m1", "m2"], archived))

    report = _inbox.triage_sweep(dry_run=False)

    assert len(archived) == 1, archived
    assert archived[0]["removeLabelIds"] == ["INBOX"], archived[0]
    assert sorted(archived[0]["ids"]) == ["m1", "m2"], archived[0]
    assert report["archived"] == 2, report


# ── The cap ──────────────────────────────────────────────────────────────────

def test_sweep_refuses_a_run_over_the_cap(monkeypatch):
    """A query that suddenly matches everything must report, not empty the inbox."""
    archived = []
    monkeypatch.setattr(_inbox, "_get_gmail_service",
                        lambda: _fake_service([f"m{i}" for i in range(50)], archived))

    report = _inbox.triage_sweep(dry_run=False, max_archive=10)

    assert archived == [], f"archived despite exceeding the cap: {archived}"
    assert report["archived"] == 0, report
    assert report["matched"] == 50, report
    assert report.get("refused"), "the report does not say it refused"


# ── The route ────────────────────────────────────────────────────────────────

def test_route_defaults_to_dry_run(monkeypatch):
    _auth(monkeypatch)
    seen = {}
    monkeypatch.setattr(_inbox, "triage_sweep",
                        lambda **kw: seen.update(kw) or {"dry_run": True})
    r = app.app.test_client().post("/api/inbox/triage-sweep",
                                   headers={"X-Console-Key": "sek"})
    assert r.get_json().get("ok") is True, r.get_json()
    assert seen.get("dry_run") is True, f"route did not default to dry run: {seen}"


def test_route_passes_dry_run_false_through(monkeypatch):
    _auth(monkeypatch)
    seen = {}
    monkeypatch.setattr(_inbox, "triage_sweep",
                        lambda **kw: seen.update(kw) or {"dry_run": False})
    app.app.test_client().post(
        "/api/inbox/triage-sweep", headers={"X-Console-Key": "sek",
                                            "Content-Type": "application/json"},
        json={"dry_run": False})
    assert seen.get("dry_run") is False, seen


def test_route_needs_the_console_key(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(_inbox, "triage_sweep",
                        lambda **kw: pytest.fail("swept without a key"))
    r = app.app.test_client().post("/api/inbox/triage-sweep")
    assert r.status_code == 401, r.status_code
