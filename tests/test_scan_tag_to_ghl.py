# tests/test_scan_tag_to_ghl.py
"""A completed voice scan must reach GHL, or its workflow can never stop.

Nothing in this codebase told GHL about a scan. The result landed in
scan_freshness and stopped there — 29 GHL contact writes in app.py, none of them
triggered by a scan or E4L event. So the "Reminder (Voice Scan)" workflow could
enrol somebody but had nothing to exit on, and kept reaching clients who had
scanned months earlier: of four sampled recipients, all four had scans predating
their first reminder, one going back to the previous October.

This tags the contact `e4l:scanned` when a NEW scan lands, giving that workflow
an exit condition and giving Glen a "has an account, never scanned" segment —
the audience the reminder emails were actually written for.
"""
import sqlite3

import pytest

from dashboard import scan_freshness as sf


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    sf.init_table(c)
    return c


def _app():
    import importlib, pathlib, sys
    repo = pathlib.Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app not importable: {e}")


# ---------------------------------------------------------------------------
# new_scanners — what makes a scan "new"
# ---------------------------------------------------------------------------

def test_a_first_ever_scan_is_new(cx):
    assert sf.new_scanners(cx, [{"email": "a@x.com", "last_scan_date": "2026-08-01"}]) == ["a@x.com"]


def test_a_repeat_of_the_same_date_is_not_new(cx):
    """THE POINT. The ingest is a cron re-sending the same rows every run, up to
    5000 at a time. Without this, every unchanged row would re-tag GHL forever."""
    rows = [{"email": "a@x.com", "last_scan_date": "2026-08-01"}]
    sf.upsert(cx, rows)
    assert sf.new_scanners(cx, rows) == []


def test_a_newer_scan_for_a_known_email_is_new(cx):
    sf.upsert(cx, [{"email": "a@x.com", "last_scan_date": "2026-08-01"}])
    assert sf.new_scanners(cx, [{"email": "a@x.com", "last_scan_date": "2026-08-09"}]) == ["a@x.com"]


def test_an_older_scan_is_not_new(cx):
    sf.upsert(cx, [{"email": "a@x.com", "last_scan_date": "2026-08-09"}])
    assert sf.new_scanners(cx, [{"email": "a@x.com", "last_scan_date": "2026-08-01"}]) == []


def test_duplicate_rows_in_one_payload_yield_one_email(cx):
    rows = [{"email": "a@x.com", "last_scan_date": "2026-08-01"},
            {"email": "A@X.com", "last_scan_date": "2026-08-01"}]
    assert sf.new_scanners(cx, rows) == ["a@x.com"]


def test_blank_email_or_date_is_ignored(cx):
    assert sf.new_scanners(cx, [{"email": "", "last_scan_date": "2026-08-01"},
                                {"email": "b@x.com", "last_scan_date": ""}]) == []


def test_it_must_be_called_before_upsert_to_mean_anything(cx):
    """Documents the ordering contract: after upsert() every row looks unchanged,
    so a caller that upserts first silently tags nobody, forever."""
    rows = [{"email": "a@x.com", "last_scan_date": "2026-08-01"}]
    sf.upsert(cx, rows)
    assert sf.new_scanners(cx, rows) == [], "post-upsert it is empty by design"


# ---------------------------------------------------------------------------
# the tag push
# ---------------------------------------------------------------------------

def test_the_tag_is_pushed_for_a_new_scanner(monkeypatch):
    app = _app()
    calls = []
    monkeypatch.setattr(app, "ghl_upsert_contact",
                        lambda email, **kw: (calls.append((email, kw)) or ("cid", True, None)))
    assert app._tag_scanned_in_ghl("a@x.com") is True
    assert calls and calls[0][0] == "a@x.com"
    assert app.E4L_SCANNED_TAG in calls[0][1]["extra_tags"]


def test_a_ghl_failure_never_breaks_scan_ingestion(monkeypatch):
    """The Biofield readiness gate reads scan_freshness. A GHL outage must not
    stop a scan being recorded."""
    app = _app()
    def boom(email, **kw):
        raise RuntimeError("GHL down")
    monkeypatch.setattr(app, "ghl_upsert_contact", boom)
    assert app._tag_scanned_in_ghl("a@x.com") is False


def test_a_reported_error_is_not_counted_as_success(monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "ghl_upsert_contact", lambda email, **kw: (None, False, "429 rate limited"))
    assert app._tag_scanned_in_ghl("a@x.com") is False


def test_no_email_no_call(monkeypatch):
    app = _app()
    calls = []
    monkeypatch.setattr(app, "ghl_upsert_contact", lambda email, **kw: calls.append(email))
    assert app._tag_scanned_in_ghl("") is False
    assert calls == []


def test_the_route_computes_new_scanners_before_upserting():
    """Ordering is the whole correctness of this feature and is invisible at
    runtime — get it backwards and it tags nobody, silently, forever."""
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "api_e4l_scan_freshness"]
    assert fn, "route is gone"
    seq = [sub.func.attr for sub in ast.walk(fn[0])
           if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
           and sub.func.attr in ("new_scanners", "upsert")]
    assert seq[:2] == ["new_scanners", "upsert"], f"wrong order: {seq}"


# ---------------------------------------------------------------------------
# the route: tag the NEW ones only
# ---------------------------------------------------------------------------

def test_the_route_tags_only_new_scanners_not_every_row(monkeypatch, tmp_path):
    """The guard that matters. The ingest is a cron re-sending the same rows —
    up to 5000 — every run. Tagging per row instead of per NEW scanner would fire
    thousands of GHL calls on every single run, forever. Covering new_scanners()
    in isolation does not catch that: the route can still loop over `rows`."""
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "CONSOLE_SECRET", "ci-secret")
    monkeypatch.setenv("CRON_SECRET", "ci-secret")
    monkeypatch.setattr(app, "_record_entry_unlock", lambda *a, **k: None)
    tagged = []
    monkeypatch.setattr(app, "ghl_upsert_contact",
                        lambda email, **kw: (tagged.append(email) or ("cid", True, None)))
    app.app.config["TESTING"] = True
    c = app.app.test_client()
    rows = {"rows": [{"email": "a@x.com", "last_scan_date": "2026-08-01"},
                     {"email": "b@x.com", "last_scan_date": "2026-08-01"}]}
    h = {"X-Cron-Secret": "ci-secret"}

    r1 = c.post("/api/e4l/scan-freshness", json=rows, headers=h)
    assert r1.status_code == 200, r1.data[:200]
    assert sorted(tagged) == ["a@x.com", "b@x.com"], tagged
    assert r1.get_json()["ghl_tagged"] == 2

    # same payload again — the cron's normal behaviour
    tagged.clear()
    r2 = c.post("/api/e4l/scan-freshness", json=rows, headers=h)
    assert r2.status_code == 200
    assert tagged == [], f"re-tagged unchanged rows: {tagged}"
    assert r2.get_json()["new_scanners"] == 0

    # a genuinely newer scan for one of them tags only that one
    tagged.clear()
    r3 = c.post("/api/e4l/scan-freshness",
                json={"rows": [{"email": "a@x.com", "last_scan_date": "2026-08-20"},
                               {"email": "b@x.com", "last_scan_date": "2026-08-01"}]},
                headers=h)
    assert r3.status_code == 200
    assert tagged == ["a@x.com"], tagged


def test_a_backfill_is_capped_and_the_cap_is_reported(monkeypatch, tmp_path, capsys):
    """A first run after an e4l backfill could present thousands of new scanners
    at once. The cap stops that becoming thousands of GHL calls in one request,
    and the overflow is LOGGED rather than dropped silently — a silent cap reads
    as "everyone got tagged" when they did not. The remainder is picked up on the
    next run, because they are still new relative to what was stored."""
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "CONSOLE_SECRET", "ci-secret")
    monkeypatch.setenv("CRON_SECRET", "ci-secret")
    monkeypatch.setattr(app, "_record_entry_unlock", lambda *a, **k: None)
    monkeypatch.setattr(app, "E4L_SCAN_TAG_MAX_PER_RUN", 5)
    tagged = []
    monkeypatch.setattr(app, "ghl_upsert_contact",
                        lambda email, **kw: (tagged.append(email) or ("cid", True, None)))
    app.app.config["TESTING"] = True
    rows = [{"email": f"u{i}@x.com", "last_scan_date": "2026-08-01"} for i in range(12)]
    r = app.app.test_client().post("/api/e4l/scan-freshness", json={"rows": rows},
                                   headers={"X-Cron-Secret": "ci-secret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["new_scanners"] == 12
    assert len(tagged) == 5, f"cap not applied: {len(tagged)}"
    assert body["ghl_tagged"] == 5
    out = capsys.readouterr().out
    assert "12 new scanners" in out and "deferred" in out, "the cap was silent"
