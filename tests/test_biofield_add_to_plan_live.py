"""A live layer related to one already on the plan is proposed for joining, after it
lands (Glen, 2026-11-07: live dictation keeps writing straight to the plan, "but
propose potential consolidation with a related existing layer when possible")."""
import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import add_chain_row
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def _app(db, layers):
    client = create_app(db, interpret_complete=lambda s, u: json.dumps({"layers": layers}),
                        scan_lookup=lambda e, client_id=None: {"found": False}).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    client.post(f"/author/{tid}/session", json={"transcript": "liver detox balanced"})
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, "Liver and gallbladder", "Liver and gallbladder",
                      "Liver Life", confirmed=1, origin="clinical")
    return client, tid


def _heads(db):
    with sqlite3.connect(db) as cx:
        return [r[0] for r in cx.execute("SELECT head FROM biofield_auth_chain ORDER BY layer,id")]


def test_a_related_live_layer_lands_and_is_proposed(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [{"layer": 1, "head": "Liver Detoxification",
                             "most_affected": "Liver", "remedy": "Detox Daily"}])
    j = client.post(f"/author/{tid}/interpret", json={}).get_json()
    assert j["added"] == 1
    # The live layer is on the plan, unjoined, and the offer names both layers.
    assert _heads(db) == ["Liver and gallbladder", "Liver Detoxification"]
    assert [(p["source_head"], p["into_head"], p["rule"]) for p in j["proposals"]] == [
        ("Liver Detoxification", "Liver and gallbladder", "same heading")]
    pending = client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"]
    assert [p["source_head"] for p in pending] == ["Liver Detoxification"]


def test_an_unrelated_live_layer_is_not_proposed(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [{"layer": 1, "head": "Kidney Meridian",
                             "most_affected": "Kidney", "remedy": "Kidney Kare"}])
    j = client.post(f"/author/{tid}/interpret", json={}).get_json()
    assert j["added"] == 1 and j["proposals"] == []
    assert client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"] == []


def test_a_live_layer_from_the_remedy_set_is_proposed_too(tmp_path, monkeypatch):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [])
    from dashboard import biofield_clinical_head_term as _ht
    # The head of an appended remedy comes from the stresses it covers; here the
    # remedy covers a liver detox stress, so its layer is named for it.
    monkeypatch.setattr(_ht, "layer_fields",
                        lambda covers, conds, lead=None: ("Liver Detoxification", "Liver", []))
    j = client.post(f"/author/{tid}/remedy-set/add-one", json={"remedy": "Detox Daily"}).get_json()
    assert j["ok"] and j["added"] == 1, j
    assert [p["into_head"] for p in j["proposals"]] == ["Liver and gallbladder"]


def test_a_live_proposal_joined_by_hand_is_closed(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [{"layer": 1, "head": "Liver Detoxification",
                             "most_affected": "Liver", "remedy": "Detox Daily"}])
    assert client.post(f"/author/{tid}/interpret", json={}).get_json()["proposals"]
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE biofield_auth_chain SET head='Liver and gallbladder'")
    assert client.get(f"/author/{tid}/add-to-plan/proposals").get_json()["proposals"] == []
