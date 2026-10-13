"""Interpret adds the live session's layers after the layers already on the intake.

Glen, 2026-10-12: Linda Maher had 2 clinical layers and 6 scan layers. The one layer
from his live session was saved as layer 1, so it showed as Layer 2 instead of 9.
"""
import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import add_chain_row, ordered_chain
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def _app(db, layers):
    def fake(system, user):
        return json.dumps({"layers": layers})
    client = create_app(db, interpret_complete=fake).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    client.post(f"/author/{tid}/session", json={"transcript": "liver meridian balanced by transform"})
    return client, tid


def _chain(db, tid):
    with sqlite3.connect(db) as cx:
        return ordered_chain(cx, tid)


def test_a_live_layer_follows_clinical_and_scan_layers(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [{"layer": 1, "head": "Liver Meridian",
                             "most_affected": "Liver Meridian", "remedy": "Transform"}])
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, "Lens", "Lens", "Crystalline Clarity", confirmed=0, origin="clinical")
        add_chain_row(cx, tid, 2, "Tear Quality", "Tears", "Moisturize", confirmed=0, origin="clinical")
        for n in range(3, 9):
            add_chain_row(cx, tid, n, f"Scan {n}", "", f"R{n}", confirmed=0, origin="scan")
    assert client.post(f"/author/{tid}/interpret", json={}).get_json()["added"] == 1
    chain = _chain(db, tid)
    last = chain[-1]
    assert last["head"] == "Liver Meridian"
    assert last["stored_layer"] == 9 and last["layer"] == 9
    assert [r["head"] for r in chain[:2]] == ["Lens", "Tear Quality"]


def test_the_sessions_own_layers_keep_their_grouping_and_order(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [
        {"layer": 1, "head": "Liver Meridian", "most_affected": "Liver",
         "remedies": [{"remedy": "Transform"}, {"remedy": "Liver Support"}]},
        {"layer": 2, "head": "Toxicity", "most_affected": "Toxicity", "remedy": "Neuro-Magnesium"}])
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, "Lens", "Lens", "Crystalline Clarity")
        add_chain_row(cx, tid, 2, "Tear Quality", "Tears", "Moisturize")
    client.post(f"/author/{tid}/interpret", json={})
    stored = [(r["head"], r["stored_layer"]) for r in _chain(db, tid)]
    assert stored == [("Lens", 1), ("Tear Quality", 2), ("Liver Meridian", 3),
                      ("Liver Meridian", 3), ("Toxicity", 4)]


def test_an_empty_intake_still_starts_at_layer_1(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client, tid = _app(db, [{"layer": 1, "head": "Liver Meridian",
                             "most_affected": "Liver Meridian", "remedy": "Transform"}])
    client.post(f"/author/{tid}/interpret", json={})
    assert [r["stored_layer"] for r in _chain(db, tid)] == [1]
