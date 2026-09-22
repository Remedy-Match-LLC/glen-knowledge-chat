"""The stress panel groups, assigns and adds by the STORED layer, not the row position.

An authored report's row "layer" is the row's position. On an intake with 7 remedies in
4 layers (Peach Goddard, 2026-09-22) the panel showed 7 "layers", auto-assign linked a
stress to one remedy of a layer instead of all of them, and a stress typed into a block
was saved to the rows of whichever STORED layer matched the block's row number.
"""
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)


_PEACH = [(1, "Liver congestion", "Liver Support"), (1, "Liver congestion", "Lipid Cleanse"),
          (2, "Gut terrain", "Microbiome"), (2, "Gut terrain", "Terrain Restore"),
          (2, "Gut terrain", "Clear the Way"), (3, "Adrenal load", "Adrenal Syntropy"),
          (4, "Sleep", "Sleep Syntropy")]


def _intake(tmp_path, interpret_complete=None):
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    from dashboard.biofield_stress import add_stress, init_stress_tables
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Peach", "peach@example.com", "2026-09-22")
        for layer, head, remedy in _PEACH:
            add_chain_row(cx, tid, layer, head, "", remedy)
        rids = [r[0] for r in cx.execute("SELECT id FROM biofield_auth_chain ORDER BY id")]
        init_stress_tables(cx)
        add_stress(cx, tid, "Kidney Driver", source="scan", balance="required")
    kw = {"fetch_profile": lambda e: {}, "fetch_recent_comms": lambda e: {}}
    if interpret_complete:
        kw["interpret_complete"] = interpret_complete
    return create_app(db, **kw).test_client(), tid, db, rids


def _placed_rids(db, label):
    with sqlite3.connect(db) as cx:
        return sorted(r[0] for r in cx.execute(
            "SELECT ls.chain_rid FROM biofield_auth_layer_stress ls "
            "JOIN biofield_auth_stress s ON s.id = ls.stress_id WHERE s.label=?", (label,)))


def test_panel_shows_one_block_per_layer(tmp_path):
    client, tid, _, rids = _intake(tmp_path)
    by = client.get(f"/author/{tid}/stresses").get_json()["data"]["by_layer"]
    assert [L["layer"] for L in by] == [1, 2, 3, 4], "REGRESSION: one block per remedy"
    assert by[1]["rids"] == rids[2:5]
    assert by[1]["remedies"] == ["Microbiome", "Terrain Restore", "Clear the Way"]


def test_auto_assign_links_every_remedy_of_the_layer(tmp_path):
    client, tid, db, rids = _intake(tmp_path, interpret_complete=lambda s, u: {
        "assignments": [{"id": 1, "layer": 2}]})
    j = client.post(f"/author/{tid}/stresses/assign-all", json={}).get_json()
    assert j["ok"] is True and j["assigned"] == 1
    assert _placed_rids(db, "Kidney Driver") == rids[2:5], (
        "REGRESSION: linked to one remedy row, or to the wrong layer")


def test_a_number_from_the_panel_resolves_to_the_same_layer(tmp_path):
    """/stress/add resolves the number by the stored column. The panel must send that."""
    client, tid, db, rids = _intake(tmp_path)
    by = client.get(f"/author/{tid}/stresses").get_json()["data"]["by_layer"]
    gut = next(L for L in by if L["head"] == "Gut terrain")
    j = client.post(f"/author/{tid}/stress/add",
                    json={"label": "Bloating", "layer": gut["layer"]}).get_json()
    assert j["ok"] is True
    assert _placed_rids(db, "Bloating") == rids[2:5]
