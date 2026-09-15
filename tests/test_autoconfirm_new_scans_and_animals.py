"""Every new analysis goes through the gate, animals never publish formulations, and the
decision log writes on Postgres. Glen, 2026-09-15.

Measured before this change:
- /admin/portal/upsert carried a client's earlier confirmed status onto every new scan, so 45
  new analyses from 2026-07-08 on went live with no quality check, and 23 of them failed it.
- Two animals, a dog and a cat, had formulation reports published that way.
- analysis_autoconfirm._log used INSERT OR REPLACE, which pgcompat does not translate. On
  Postgres every decision raised, and no log row was ever written.
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
    from dashboard import analysis_autoconfirm as ac
    from dashboard import client_portal as _cp
    from dashboard import client_species as _cs
    from dashboard import pgcompat
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)

CATALOG = {
    "vitality": {"name": "Vitality", "qty_pricing": True},
    "ed9-muscle-energetic-driver-infoceutical": {"name": "ED9 Muscle Energetic Driver Infoceutical"},
}


def _setup(mp, tmp):
    db = str(tmp / "chat_log.db")
    mp.setenv("DATA_DIR", str(tmp))
    mp.setattr(app, "LOG_DB", db, raising=False)
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)
    mp.setattr(app, "ANALYSIS_AUTOCONFIRM_ENABLED", True, raising=False)
    mp.setattr(app, "ANALYSIS_AUTOCONFIRM_SAMPLE_PCT", "0", raising=False)
    from dashboard import biofield_portal_publish as bpp
    mp.setattr(bpp, "load_catalog", lambda: CATALOG)
    by_name = {v["name"].lower(): k for k, v in CATALOG.items()}
    mp.setattr(bpp, "resolve_remedy_slug", lambda n, c: by_name.get((n or "").lower()))
    cx = sqlite3.connect(db)
    _cp.init_client_portal_table(cx)
    cx.close()
    return db


def _publish(email, scan_date, remedy):
    body = {"email": email, "name": "T", "scan_date": scan_date,
            "content": {"biofield_status": "ai_draft", "greeting": "Aloha.",
                        "layers": [{"title": "Layer", "remedy": remedy,
                                    "dosage": "1", "frequency": "daily"}]}}
    return app.app.test_client().post(
        "/admin/portal/upsert", headers={"X-Console-Key": "sek", "Content-Type": "application/json"},
        data=json.dumps(body))


def _report_status(db, email, scan_date):
    row = sqlite3.connect(db).execute(
        "SELECT status FROM portal_biofield_reports WHERE email=? AND scan_date=?",
        (email, scan_date)).fetchone()
    return row[0] if row else None


def _decision(db, email, scan_date):
    row = sqlite3.connect(db).execute(
        "SELECT decision FROM analysis_autoconfirm_log WHERE email=? AND scan_date=?",
        (email, scan_date)).fetchone()
    return row[0] if row else None


def test_the_log_write_is_postgres_safe_and_updates_in_place(tmp_path):
    seen = []

    class _Rec:
        def execute(self, sql, params=()):
            seen.append(sql)

        def commit(self):
            pass

    ac._log(_Rec(), "a@x.com", "2026-09-01", "held_quality", ["r"], False, "now")
    assert "OR REPLACE" not in seen[0].upper()
    assert "ON CONFLICT (email, scan_date) DO UPDATE" in pgcompat.translate_sql(seen[0])

    cx = sqlite3.connect(str(tmp_path / "log.db"))
    ac.init_autoconfirm_log(cx)
    ac._log(cx, "a@x.com", "2026-09-01", "held_quality", ["r"], False, "t1")
    ac._log(cx, "A@x.com ", "2026-09-01", "confirmed", [], False, "t2")
    assert cx.execute("SELECT decision, created_at FROM analysis_autoconfirm_log").fetchall() == \
        [("confirmed", "t2")]


def test_a_returning_confirmed_clients_new_scan_goes_through_the_gate(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _publish("back@x.com", "2026-07-01", "Vitality")
    assert _report_status(db, "back@x.com", "2026-07-01") == "confirmed"
    _publish("back@x.com", "2026-08-01", "Not A Product")
    assert _report_status(db, "back@x.com", "2026-08-01") == "ai_draft"
    assert _decision(db, "back@x.com", "2026-08-01") == "held_quality"


def test_a_resync_of_the_same_confirmed_scan_stays_confirmed(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _publish("same@x.com", "2026-07-01", "Vitality")
    _publish("same@x.com", "2026-07-01", "Not A Product")
    assert _report_status(db, "same@x.com", "2026-07-01") == "confirmed"


def test_an_animal_report_naming_a_formulation_is_held(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    _cs.init_table(cx)
    _cs.upsert(cx, "dog@x.com", "Dog", "Rex")
    cx.close()
    _publish("dog@x.com", "2026-08-29", "Vitality")
    assert _report_status(db, "dog@x.com", "2026-08-29") == "ai_draft"
    assert _decision(db, "dog@x.com", "2026-08-29") == "held_animal_formulation"


def test_an_animal_report_of_infoceuticals_can_publish(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    _cs.init_table(cx)
    _cs.upsert(cx, "cat@x.com", "Cat", "Sasha")
    cx.close()
    _publish("cat@x.com", "2026-08-30", "ED9 Muscle Energetic Driver Infoceutical")
    assert _report_status(db, "cat@x.com", "2026-08-30") == "confirmed"


def test_a_human_client_is_not_checked_for_formulations(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    _publish("person@x.com", "2026-08-01", "Vitality")
    assert _report_status(db, "person@x.com", "2026-08-01") == "confirmed"


def test_formulation_alternatives_count_too():
    content = {"layers": [{"title": "L", "remedy": "ED9 Muscle Energetic Driver Infoceutical",
                           "alternatives": [{"name": "Vitality"}, "Nope"]}]}
    by_name = {v["name"].lower(): k for k, v in CATALOG.items()}
    reasons = ac.animal_formulation_reasons(
        content, resolve_slug=lambda n: by_name.get(n.lower()),
        is_formulation=lambda s: bool(CATALOG[s].get("qty_pricing")))
    assert reasons == ["layer 0: animal report names a formulation ('Vitality')"]
