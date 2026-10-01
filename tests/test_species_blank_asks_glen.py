"""A blank species asks Glen before the Intake proposes remedies.

Glen, 2026-10-01: "if species is blank, ask me first". Skylar, a dog, had a blank species
in e4l.db, read as a person, and Import Reveal put Functional Formulations in her chain.
The answer is recorded so he is asked once.
"""
import sqlite3

import pytest

from biofield_local_app import create_app
import dashboard.biofield_reveal_import as RI
from dashboard import biofield_e4l as BE
from dashboard import biofield_report_html as H

_FRESH = {"found": True, "scan_id": 900, "scan_date": "2026-06-22", "days_ago": 3,
          "fresh": True, "layers": [{"n": 1, "title": "Oxidative load", "summary": "",
                                     "most_affected": "Cell", "remedy_name": "Neuro Magnesium"}]}
_NONE = {"found": False, "scan_id": None, "scan_date": None, "days_ago": None,
         "fresh": False, "layers": []}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _e4l(tmp_path, species):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER, name TEXT, email TEXT, "
               "species TEXT, animal_name TEXT, detail_scraped INTEGER)")
    cx.execute("INSERT INTO e4l_clients VALUES (1, 'Pet', 'pet@x.com', ?, '', 1)", (species,))
    cx.commit()
    cx.close()
    return p


def _client(tmp_path, species, monkeypatch, seen=None):
    def synth(*a, **k):
        if seen is not None:
            seen.append(k.get("is_animal"))
        return _FRESH
    monkeypatch.setattr(RI, "synthesize_reveal_layers", synth)
    db = str(tmp_path / "chat_log.db")
    c = create_app(db, scan_lookup=lambda e: _NONE, e4l_db=_e4l(tmp_path, species)).test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Pet", "email": "pet@x.com", "date": "2026-06-25"})
    return c, tid, db


def _rows(db):
    return sqlite3.connect(db).execute("SELECT COUNT(*) FROM biofield_auth_chain").fetchone()[0]


@pytest.mark.parametrize("species", [None, ""])
def test_every_remedy_path_asks_when_species_is_blank(tmp_path, monkeypatch, species):
    c, tid, db = _client(tmp_path, species, monkeypatch)
    for path, body in ((f"/author/{tid}/e4l/import-reveal", {}),
                       (f"/author/{tid}/remedy-set/apply-to-chain", {"remedies": ["Heart Health"]}),
                       (f"/author/{tid}/remedy-set/add-one", {"remedy": "Heart Health"}),
                       (f"/author/{tid}/layer/1/select", {"remedy": "Heart Health"})):
        j = c.post(path, json=body).get_json()
        assert j.get("needs_species") is True, path
    assert _rows(db) == 0, "a remedy was written before Glen answered"


def test_an_answer_of_animal_is_kept_and_imports_as_an_animal(tmp_path, monkeypatch):
    seen = []
    c, tid, db = _client(tmp_path, None, monkeypatch, seen)
    assert c.post(f"/author/{tid}/species", json={"species": "dog"}).get_json() == \
        {"ok": True, "species": "Dog"}
    assert BE.species_from_e4l(str(tmp_path / "e4l.db"), "Pet@X.com") == "Dog"
    j = c.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] is True
    assert seen == [True]


def test_an_answer_of_person_imports_as_a_person(tmp_path, monkeypatch):
    seen = []
    c, tid, db = _client(tmp_path, None, monkeypatch, seen)
    c.post(f"/author/{tid}/species", json={"species": "Human"})
    assert c.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()["ok"] is True
    assert seen == [False]


def test_a_species_from_energy4life_is_never_asked_and_outranks_an_answer(tmp_path, monkeypatch):
    seen = []
    c, tid, db = _client(tmp_path, "Cat", monkeypatch, seen)
    BE.record_species_answer(str(tmp_path / "e4l.db"), "pet@x.com", "Human")
    j = c.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] is True and seen == [True]


def test_an_empty_answer_is_refused(tmp_path, monkeypatch):
    c, tid, db = _client(tmp_path, None, monkeypatch)
    r = c.post(f"/author/{tid}/species", json={"species": "  "})
    assert r.status_code == 400
    assert c.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json().get("needs_species")


def test_the_page_asks_then_resends_the_same_request():
    js = H._AUTHOR_JS
    assert "if(j&&j.needs_species){if(!(await askSpecies()))return j;j=await post0(p,b)}" in js
    assert "/author/__TID__/species" in js


def test_import_button_shows_it_is_working_and_ignores_a_second_click():
    js = H._AUTHOR_JS
    assert "if(window.__impBusy)return;" in js
    assert "impStat('Importing\u2026')" in js
    src = open(H.__file__).read()
    assert "id=impBtn onclick=importReveal()" in src and "<span id=impStat" in src
