# tests/test_biofield_mine_comms_routes.py
import sqlite3
import pytest
from biofield_local_app import create_app


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    # dashboard/__init__.py captures CONSOLE_SECRET at import; reloading
    # app does not reset it, so clear the copy the guard actually reads.
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}
_COMMS = {"intake_summary": "first name: Jane\n  Energy?: Low",
          "recent_inquiries": [{"main_challenge": "fatigue", "main_goal": "more energy"}],
          "recent_queries": [{"question": "why tired"}]}


def _app(db, comms, stresses):
    import json as _j
    return create_app(db, scan_lookup=lambda e: _NONE,
                      fetch_profile=lambda e: {},
                      fetch_recent_comms=lambda e: comms if e == "j@x.com" else {},
                      interpret_complete=lambda s, u: _j.dumps({"stresses": stresses}))


def _new(client, email):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "J", "email": email, "date": "2026-06-25"})
    return tid


def test_mine_comms_adds_comm_stresses(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db, _COMMS, ["Chronic fatigue", "Poor sleep"]).test_client()
    tid = _new(client, "j@x.com")
    j = client.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert "error" not in j
    data = client.get(f"/author/{tid}/stresses").get_json()["data"]
    labels = {x["label"] for x in data["active"] + data["balanced"]}
    sources = {x["source"] for x in data["active"] + data["balanced"]}
    assert {"Chronic fatigue", "Poor sleep"} <= labels and "comm" in sources


def test_mine_comms_no_email(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db, _COMMS, ["X"]).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    j = client.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert j["added"] == 0 and "error" in j


def test_mine_comms_empty(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db, {}, []).test_client()
    tid = _new(client, "nobody@x.com")
    assert client.post(f"/author/{tid}/mine-comms", json={}).get_json()["added"] == 0


def test_header_save_mines_comms_run_once(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db, _COMMS, ["Chronic fatigue"]).test_client()
    tid = _new(client, "j@x.com")   # header-save hook should have mined comms already
    data = client.get(f"/author/{tid}/stresses").get_json()["data"]
    labels = {x["label"] for x in data["active"] + data["balanced"]}
    assert "Chronic fatigue" in labels


def _app_with(db, *, comms=None, profile=None, stresses=()):
    import json as _j
    return create_app(db, scan_lookup=lambda e: _NONE,
                      fetch_profile=lambda e: profile,
                      fetch_recent_comms=lambda e: comms,
                      interpret_complete=lambda s, u: _j.dumps({"stresses": list(stresses)}))


def test_a_failed_comms_fetch_says_so_instead_of_finding_nothing(tmp_path):
    """Glen, 2026-10-16 (Josie Gagnon): a failure must not read as no information."""
    client = _app_with(str(tmp_path / "c.db"), comms=None, profile={}).test_client()
    tid = _new(client, "j@x.com")
    j = client.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert j["added"] == 0 and j["error"].startswith("Could not reach the console")


def test_a_failed_profile_fetch_says_so_instead_of_finding_nothing(tmp_path):
    client = _app_with(str(tmp_path / "c.db"), comms={}, profile=None).test_client()
    tid = _new(client, "j@x.com")
    j = client.post(f"/author/{tid}/mine-profile", json={}).get_json()
    assert j["added"] == 0 and j["error"].startswith("Could not reach the console")


def test_an_empty_profile_and_inbox_are_not_errors(tmp_path):
    client = _app_with(str(tmp_path / "c.db"), comms={}, profile={}).test_client()
    tid = _new(client, "j@x.com")
    assert client.post(f"/author/{tid}/mine-comms", json={}).get_json() == {"added": 0}
    assert client.post(f"/author/{tid}/mine-profile", json={}).get_json() == {"added": 0}


def test_a_section_the_server_could_not_read_is_named_and_the_rest_still_mined(tmp_path):
    comms = dict(_COMMS, unavailable=["feedback"])
    client = _app_with(str(tmp_path / "c.db"), comms=comms, profile={},
                       stresses=["Chronic fatigue"]).test_client()
    tid = _new(client, "j@x.com")
    j = client.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert j["error"] == "Some recent comms could not be read (feedback)."
    # Header save already mined once, so the stress may be on the test before this click.
    data = client.get(f"/author/{tid}/stresses").get_json()["data"]
    assert "Chronic fatigue" in {x["label"] for x in data["active"] + data["balanced"]}
