"""The two buttons: one program from all three stress sources, in Glen's order.

Glen, 2026-09-17: *"2 buttons: full (narrow, bigger program) - vs minimum (wide,
minimal program)."*

Both PROPOSE. Neither writes until he confirms, because the causal chain is the
intake's core clinical artifact. That is the same rule #1717 set for Balance All.
"""
import json
import sqlite3
import pytest

from biofield_local_app import create_app


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NO_SCAN = {"status": "none", "found": False, "findings": [],
            "days_ago": None, "fresh": False}

# What the transcript interpreter returns: one layer, its own remedy.
_SPIRIT = {"header": "", "phase": None, "location": "",
           "layers": [{"layer": 1, "head": "Grief", "most_affected": "Grief",
                       "remedy": "Heart Health"}]}


def _app(db, spirit=None):
    return create_app(db, scan_lookup=lambda e: _NO_SCAN,
                      fetch_profile=lambda e: {},
                      fetch_recent_comms=lambda e: {},
                      interpret_complete=lambda s, u: json.dumps(spirit or _SPIRIT))


def _new(client):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header",
                json={"name": "J", "email": "j@x.com", "date": "2026-09-17"})
    client.post(f"/author/{tid}/session", json={"transcript": "she spoke of grief"})
    return tid


def _add_stress(client, tid, label):
    client.post(f"/author/{tid}/stress/add", json={"label": label})


def _chain_len(db, tid):
    with sqlite3.connect(db) as cx:
        n = int(str(tid).lstrip("a") or 0)
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_chain WHERE test_id=?",
                          (n,)).fetchone()[0]


def _stress_count(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0]


def test_a_proposal_writes_nothing(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    before_chain, before_stress = _chain_len(db, tid), _stress_count(db)
    j = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    assert j["ok"] and j["proposed"] is True
    assert _chain_len(db, tid) == before_chain, "the proposal wrote to the chain"
    assert _stress_count(db) == before_stress, "the proposal wrote a stress"


def test_a_proposal_runs_spirit_then_mind_then_body(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    j = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    assert [s["stage"] for s in j["stages"]] == ["spirit", "mind", "body"]
    assert "Heart Health" in j["remedies"]


def test_the_transcript_layer_is_held_not_written_until_apply(tmp_path):
    db = str(tmp_path / "c.db")
    client = _app(db).test_client()
    tid = _new(client)
    client.post(f"/author/{tid}/program", json={"mode": "full"})
    assert _chain_len(db, tid) == 0
    client.post(f"/author/{tid}/program", json={"mode": "full", "apply": True})
    assert _chain_len(db, tid) >= 1


def test_minimum_never_proposes_more_than_full(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    _add_stress(client, tid, "Fatigue")
    full = client.post(f"/author/{tid}/program", json={"mode": "full"}).get_json()
    lean = client.post(f"/author/{tid}/program", json={"mode": "minimum"}).get_json()
    assert set(lean["remedies"]) <= set(full["remedies"])


def test_an_unknown_mode_is_refused(tmp_path):
    client = _app(str(tmp_path / "c.db")).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/program", json={"mode": "whatever"})
    assert r.status_code == 400
    assert _chain_len(str(tmp_path / "c.db"), tid) == 0
