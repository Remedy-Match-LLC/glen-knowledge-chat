"""The console "set to draft" control, 2026-09-15.

Glen: "add a draft button". It takes a client's report date back to draft: the portal shows
it blurred whatever its stored status, and only a later Publish of that date releases it.
A reveal-only date has no status of its own, so the hold is what un-publishes it.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

try:
    import app
    import dashboard
    from dashboard import client_360
    from dashboard import client_portal as _cp
    from dashboard import portal_biofield_reports as _pbr
    from dashboard import report_holds as _rh
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)

EMAIL = "pet@x.com"
DATE = "2026-08-29"
LAYER = {"title": "Layer", "remedy": "Vitality", "dosage": "1", "frequency": "daily"}


def _setup(mp, tmp):
    db = str(tmp / "chat_log.db")
    mp.setenv("DATA_DIR", str(tmp))
    mp.setattr(app, "LOG_DB", db, raising=False)
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)
    cx = sqlite3.connect(db)
    _cp.init_client_portal_table(cx)
    _pbr.init_table(cx)
    cx.close()
    return db


def _post(path, body, key="sek"):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Console-Key"] = key
    return app.app.test_client().post(path, headers=headers, data=json.dumps(body))


def _confirmed_report(db):
    cx = sqlite3.connect(db)
    _pbr.upsert_report(cx, EMAIL, DATE, "1", {"layers": [LAYER]}, "confirmed")
    cx.close()


def test_the_route_needs_the_console_key(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _post("/api/console/client/report-draft", {"email": EMAIL, "scan_date": DATE}, key=None).status_code == 401


def test_a_date_with_nothing_on_it_is_404(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = _post("/api/console/client/report-draft", {"email": EMAIL, "scan_date": DATE})
    assert r.status_code == 404


def test_set_to_draft_holds_the_date_and_drafts_the_report(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _confirmed_report(db)
    r = _post("/api/console/client/report-draft", {"email": EMAIL, "scan_date": DATE})
    assert r.status_code == 200
    assert r.get_json()["report_status_before"] == "confirmed"
    cx = sqlite3.connect(db)
    assert _pbr.get_report(cx, EMAIL, DATE)["status"] == "ai_draft"
    assert _rh.is_held(cx, EMAIL, DATE)
    cx.row_factory = sqlite3.Row
    rows = client_360._reports(cx, EMAIL)
    assert rows[0]["held"] is True and rows[0]["status"] == "ai_draft"


def test_publishing_the_date_again_releases_the_hold(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _confirmed_report(db)
    _post("/api/console/client/report-draft", {"email": EMAIL, "scan_date": DATE})
    r = _post("/api/console/biofield-portal",
              {"email": EMAIL, "name": "Pet", "scan_date": DATE, "content": {"layers": [LAYER]}})
    assert r.status_code == 200
    cx = sqlite3.connect(db)
    assert not _rh.is_held(cx, EMAIL, DATE)
    assert _pbr.get_report(cx, EMAIL, DATE)["status"] == "confirmed"


def test_an_autoconfirm_resync_does_not_release_the_hold(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _confirmed_report(db)
    _post("/api/console/client/report-draft", {"email": EMAIL, "scan_date": DATE})
    monkeypatch.setattr(app, "ANALYSIS_AUTOCONFIRM_ENABLED", True, raising=False)
    monkeypatch.setattr(app, "ANALYSIS_AUTOCONFIRM_SAMPLE_PCT", "0", raising=False)
    from dashboard import biofield_portal_publish as bpp
    monkeypatch.setattr(bpp, "resolve_remedy_slug", lambda n, c: "vitality")
    _post("/admin/portal/upsert", {"email": EMAIL, "name": "Pet", "scan_date": DATE,
                                   "content": {"biofield_status": "ai_draft", "layers": [LAYER]}})
    assert _rh.is_held(sqlite3.connect(db), EMAIL, DATE)


def test_the_hold_store_round_trips():
    cx = sqlite3.connect(":memory:")
    _rh.init_table(cx)
    assert _rh.hold(cx, " Pet@X.com ", DATE, "console")
    assert _rh.hold(cx, "pet@x.com", DATE, "console")          # idempotent
    assert _rh.held_dates(cx, "PET@x.com") == {DATE}
    _rh.release(cx, "pet@x.com", DATE)
    assert not _rh.is_held(cx, "pet@x.com", DATE)
    assert not _rh.hold(cx, "", DATE)
