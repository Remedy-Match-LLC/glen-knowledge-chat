"""Animal Biofield reports, built from the scan's own infoceuticals. Glen, 2026-09-15:
- "publish animal reports recommending the infoceuticals recommended in the e4l report, not
  our functional formulations";
- "animal Biofield report reuses that same infoceutical list";
- "use our standard wording to build up from 1 drop by one additional drop per day according
  to tolerance, up to a maximum of 15 drops per day".
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
    from dashboard import client_portal as _cp
    from dashboard import client_species as _cs
    from dashboard import portal_biofield_reports as _pbr
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)

DOG = "dog@x.com"
DATE = "2026-09-13"
PRODUCTS = {
    "ed9-muscle-energetic-driver-infoceutical": {"name": "ED9 Muscle Energetic Driver Infoceutical"},
    "bfa-big-field-aligner-infoceutical": {"name": "BFA Big Field Aligner Infoceutical"},
}
RECS = {"scan_date": DATE, "scan_dates": [DATE],
        "infoceuticals": [
            {"code": "BFA", "label": "Big Field Aligner (BFA)", "rank": 1,
             "slug": "bfa-big-field-aligner-infoceutical"},
            {"code": "ED9", "label": "ED9 - Muscle", "rank": 2,
             "slug": "ed9-muscle-energetic-driver-infoceutical"}],
        "mihealth": [{"code": "ER2", "label": "ER2 - Large Intestine", "rank": 4}]}
MISSING_RECS = dict(RECS, infoceuticals=RECS["infoceuticals"] + [
    {"code": "EX0", "label": "EX0 - No product", "rank": 3, "slug": ""}])


def _setup(mp, tmp, species="Dog", recs=RECS):
    db = str(tmp / "chat_log.db")
    mp.setenv("DATA_DIR", str(tmp))
    mp.setattr(app, "LOG_DB", db, raising=False)
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)
    mp.setattr(app, "ANALYSIS_AUTOCONFIRM_ENABLED", True, raising=False)
    mp.setattr(app, "ANALYSIS_AUTOCONFIRM_SAMPLE_PCT", "0", raising=False)
    mp.setattr(app, "_scan_recommendations_for", lambda email, scan_date=None: recs)
    mp.setattr(app, "_get_product", lambda slug: PRODUCTS.get(slug))
    from dashboard import biofield_portal_publish as bpp
    catalog = dict(PRODUCTS, vitality={"name": "Vitality", "qty_pricing": True})
    mp.setattr(bpp, "load_catalog", lambda: catalog)
    by_name = {v["name"].lower(): k for k, v in catalog.items()}
    mp.setattr(bpp, "resolve_remedy_slug", lambda n, c: by_name.get((n or "").lower()))
    cx = sqlite3.connect(db)
    _cp.init_client_portal_table(cx)
    _cs.init_table(cx)
    if species:
        _cs.upsert(cx, DOG, species, "Rex")
    cx.close()
    return db


def _post(body, key="sek"):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Console-Key"] = key
    return app.app.test_client().post("/api/console/animal-report", headers=headers, data=json.dumps(body))


def _report(db):
    cx = sqlite3.connect(db)
    _pbr.init_table(cx)
    return _pbr.get_report(cx, DOG, DATE)


def test_an_animal_gets_its_infoceuticals_with_the_standard_dosing(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    r = _post({"email": DOG, "name": "Rex", "scan_date": DATE})
    assert r.status_code == 200, r.get_json()
    rep = _report(db)
    layers = rep["content"]["layers"]
    assert [L["remedy"] for L in layers] == ["BFA Big Field Aligner Infoceutical",
                                             "ED9 Muscle Energetic Driver Infoceutical"]
    assert [L["title"] for L in layers] == ["Big Field Aligner (BFA)", "ED9 - Muscle"]
    assert all(L["dosing"] == app.ANIMAL_INFOCEUTICAL_DOSING for L in layers)
    assert "1 drop" in app.ANIMAL_INFOCEUTICAL_DOSING and "15 drops per day" in app.ANIMAL_INFOCEUTICAL_DOSING
    assert rep["status"] == "confirmed"          # complete infoceutical report passes the gate
    assert {i["slug"] for i in rep["content"]["reorder_items"]} == set(PRODUCTS)


def test_a_person_is_refused(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path, species="Human")
    assert _post({"email": DOG, "scan_date": DATE}).status_code == 409
    assert _report(db) is None


def test_a_client_with_no_species_record_is_refused(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path, species=None)
    assert _post({"email": DOG, "scan_date": DATE}).status_code == 409
    assert _report(db) is None


def test_an_infoceutical_with_no_product_holds_the_report_instead_of_dropping_it(monkeypatch, tmp_path):
    """Glen: recommend what the E4L report recommends. A missing product must not shorten it."""
    db = _setup(monkeypatch, tmp_path, recs=MISSING_RECS)
    r = _post({"email": DOG, "scan_date": DATE})
    assert r.status_code == 200
    assert r.get_json()["autoconfirm"] == "held_missing_infoceutical"
    rep = _report(db)
    assert rep["status"] == "ai_draft"
    assert [L["remedy"] for L in rep["content"]["layers"]][-1] == "EX0 - No product"
    row = sqlite3.connect(db).execute(
        "SELECT decision, reasons FROM analysis_autoconfirm_log WHERE email=? AND scan_date=?",
        (DOG, DATE)).fetchone()
    assert row[0] == "held_missing_infoceutical" and "EX0 - No product" in row[1]


def test_no_infoceuticals_is_refused(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path, recs={"scan_date": DATE, "infoceuticals": [], "mihealth": []})
    assert _post({"email": DOG, "scan_date": DATE}).status_code == 409
    assert _report(db) is None


def test_a_different_scan_date_is_refused(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path, recs=dict(RECS, scan_date="2026-08-29"))
    assert _post({"email": DOG, "scan_date": DATE}).status_code == 409
    assert _report(db) is None


def test_the_route_needs_the_console_key(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _post({"email": DOG, "scan_date": DATE}, key=None).status_code == 401
