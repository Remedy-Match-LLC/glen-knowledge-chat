"""Formulation-map curation page + API routes (add / remove / reorder)."""
import sqlite3
import pytest
from biofield_local_app import create_app

_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    # dashboard/__init__.py captures CONSOLE_SECRET at import; reloading
    # app does not reset it, so clear the copy the guard actually reads.
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _e4l(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_items(code TEXT, category TEXT, subcategory TEXT, name TEXT, "
               "full_name TEXT, e4l_description TEXT, clinical_notes TEXT, sort_order INT)")
    cx.execute("INSERT INTO e4l_items(code,name,sort_order) VALUES('ED5','Circulation Driver',1)")
    cx.execute("INSERT INTO e4l_items(code,name,full_name,sort_order) "
               "VALUES('ER68','Rejuvenator 68','ER68',2)")
    cx.commit()
    cx.close()
    return p


def _client(tmp_path):
    db_path = str(tmp_path / "c.db")
    return create_app(db_path, e4l_db=_e4l(tmp_path), scan_lookup=lambda e: _NONE).test_client()


def test_page_renders_codes(tmp_path):
    r = _client(tmp_path).get("/formulation-map")
    assert r.status_code == 200
    assert b"Formulation map" in r.data and b"ED5" in r.data


def test_add_remedy_input_filters_catalog_by_entered_string(tmp_path):
    html = _client(tmp_path).get("/formulation-map").get_data(as_text=True)
    assert "oninput='fmMatch(this)'" in html
    assert "k.indexOf(q)<0" in html
    assert "Miasmatox Homeopathic Complex in Terrain Restore" in html
    assert "class=fmmatches" in html
    assert "fmAdd(fmRow(this).querySelector('.fminput'))" in html


def test_page_corrects_er_placeholder_to_anatomical_name(tmp_path):
    db_path = _e4l(tmp_path)
    c = create_app(str(tmp_path / "c.db"), e4l_db=db_path,
                   scan_lookup=lambda e: _NONE).test_client()
    html = c.get("/formulation-map").get_data(as_text=True)
    assert "ER68" in html and "Acoustic Nerve" in html
    with sqlite3.connect(db_path) as cx:
        assert cx.execute("SELECT name,full_name FROM e4l_items WHERE code='ER68'").fetchone() == (
            "Acoustic Nerve", "Acoustic Nerve Rejuvenator")


def test_add_reorder_remove_roundtrip(tmp_path):
    c = _client(tmp_path)
    c.post("/api/formulation-map/add", json={"code": "ED5", "remedy": "Heart Health"})
    j = c.post("/api/formulation-map/add", json={"code": "ED5", "remedy": "Vein Support"}).get_json()
    assert [m["name"] for m in j["mappings"]] == ["Heart Health", "Vein Support"]
    fids = [m["formulation_id"] for m in j["mappings"]]
    j2 = c.post("/api/formulation-map/reorder", json={"code": "ED5", "order": [fids[1], fids[0]]}).get_json()
    assert [m["name"] for m in j2["mappings"]] == ["Vein Support", "Heart Health"]
    j3 = c.post("/api/formulation-map/remove", json={"code": "ED5", "formulation_id": fids[0]}).get_json()
    assert [m["name"] for m in j3["mappings"]] == ["Vein Support"]
