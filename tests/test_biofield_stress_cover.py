"""#3: layer cards show the stresses their remedies cover, and dragging an
unbalanced stress onto a layer marks it covered (adds a remedy<->code link)."""
import sqlite3

import pytest

from dashboard import biofield_stress as st
from dashboard.biofield_authoring import add_chain_row
from dashboard.biofield_report_html import render_author_html, render_stress_panel
from biofield_local_app import create_app


def _setup(cx):
    st.init_stress_tables(cx)
    rid = add_chain_row(cx, "9", 1, "Head", "", "Nerve Pulse", "", "", "")
    st.add_stress(cx, "9", "Loose ends", source="scan", balance="required")
    sid = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()[0]
    return rid, sid


def test_cover_stress_moves_it_under_the_layer():
    cx = sqlite3.connect(":memory:")
    rid, sid = _setup(cx)
    chain = [{"layer": 1, "head": "Head", "remedy": "Nerve Pulse"}]
    d0 = st.list_stresses(cx, "9", chain)
    assert {s["code"] for s in d0["unassigned"]} == {"loose ends"}
    assert d0["by_layer"][0]["stresses"] == []
    assert st.cover_stress(cx, "9", sid, [rid]) == "loose ends"
    d1 = st.list_stresses(cx, "9", chain)
    assert d1["unassigned"] == []
    assert {s["code"] for s in d1["by_layer"][0]["stresses"]} == {"loose ends"}


def test_cover_stress_ignores_missing_or_remedyless():
    cx = sqlite3.connect(":memory:")
    rid, sid = _setup(cx)
    assert st.cover_stress(cx, "9", 999999, [rid]) is None       # no such stress
    empty = add_chain_row(cx, "9", 2, "H2", "", "", "", "", "")   # remedy-less row
    st.cover_stress(cx, "9", sid, [empty])                        # no-op, no crash
    d = st.list_stresses(cx, "9", [{"layer": 1, "head": "Head", "remedy": "Nerve Pulse"}])
    assert {s["code"] for s in d["unassigned"]} == {"loose ends"} # still unassigned


def test_consolidate_layer_balances_moves_all_coverage():
    cx = sqlite3.connect(":memory:")
    rid, sid = _setup(cx)
    target_rid = add_chain_row(cx, "9", 2, "Target", "", "Calm Current", "", "", "")
    st.add_stress(cx, "9", "Second stress", source="scan", balance="required")
    sid2 = cx.execute("SELECT id FROM biofield_auth_stress ORDER BY id DESC").fetchone()[0]
    st.cover_stress(cx, "9", sid, [rid])
    st.cover_stress(cx, "9", sid2, [rid])

    assert st.consolidate_layer_balances(cx, "9", 1, 2) == 2
    rows = cx.execute("SELECT remedy,code FROM biofield_auth_remedy_coverage ORDER BY code").fetchall()
    assert rows == [("calm current", "loose ends"), ("calm current", "second stress")]
    assert cx.execute(
        "SELECT stress_id,chain_rid FROM biofield_auth_layer_stress ORDER BY stress_id"
    ).fetchall() == [(sid, target_rid), (sid2, target_rid)]
    chain = [
        {"layer": 1, "head": "Head", "remedy": "Nerve Pulse", "rid": rid},
        {"layer": 2, "head": "Target", "remedy": "Calm Current", "rid": target_rid},
    ]
    grouped = st.list_stresses(cx, "9", chain)["by_layer"]
    assert grouped[0]["stresses"] == []
    assert {item["id"] for item in grouped[1]["stresses"]} == {sid, sid2}


def test_consolidate_layer_balances_rejects_same_or_missing_target():
    cx = sqlite3.connect(":memory:")
    _setup(cx)
    with pytest.raises(ValueError, match="must differ"):
        st.consolidate_layer_balances(cx, "9", 1, 1)
    with pytest.raises(ValueError, match="target layer has no remedies"):
        st.consolidate_layer_balances(cx, "9", 1, 2)


def test_card_shows_covered_chips_and_unassigned_is_draggable():
    rep = {"test_id": "a1", "client": {"name": "J", "email": ""}, "date": "",
           "layers": [{"layer": 1, "head": "H", "most_affected": "", "remedy": "R",
                       "rid": 5, "confirmed": 1}], "schedule": {"slots": [], "entries": []}}
    html = render_author_html(rep, [], "", covered_by_layer={1: [{"code": "ED1", "label": "Membrane"}]})
    assert "class=covered" in html and "balances:" in html and "ED1" in html
    assert "add balanced stress to layer 1" in html
    assert "addLayerStress(this,1)" in html
    assert "function coverStress" in html and "function stressDragStart" in html
    panel = render_stress_panel({"by_layer": [
        {"layer": 3, "head": "Neural", "rids": [41, 42], "stresses": []}], "unassigned": [
        {"id": 7, "code": "MR2", "label": "Calm", "balance": "required", "balanced": False}]})
    assert "stressDragStart(event,7)" in panel and "class=sdrag" in panel
    assert "#3 — Neural" in panel
    assert "value='41,42'" in panel
    assert "balanceToLayer(7" in panel


def test_each_layer_card_has_consolidation_picker_for_other_layers():
    rep = {"test_id": "a1", "client": {"name": "J", "email": ""}, "date": "",
           "layers": [
               {"layer": 1, "head": "H1", "most_affected": "", "remedy": "R1", "rid": 5},
               {"layer": 2, "head": "H2", "most_affected": "", "remedy": "R2", "rid": 6}],
           "schedule": {"slots": [], "entries": []}}
    html = render_author_html(rep, [], "")
    assert html.count("Consolidate balances</button>") == 2
    assert "consolidateBalances(1" in html and "consolidateBalances(2" in html


def test_cover_route(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    # dashboard/__init__.py captures CONSOLE_SECRET at import; reloading
    # app does not reset it, so clear the copy the guard actually reads.
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "c.db")
    client = create_app(db, scan_lookup=lambda e: {"status": "none", "found": False,
                                                   "findings": [], "fresh": False}).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    rid = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "H", "remedy": "Nerve Pulse"}).get_json()["rid"]
    with sqlite3.connect(db) as cx:
        st.add_stress(cx, tid, "Loose ends", source="scan", balance="required")
        sid = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()[0]
    j = client.post(f"/author/{tid}/stress/{sid}/cover", json={"rids": [rid]}).get_json()
    assert j["ok"] is True and j["code"] == "loose ends"
    # editor page now renders without error and lists the covered stress inline
    assert client.get(f"/author/{tid}").status_code == 200


def test_consolidate_balances_route(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d; monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "c.db")
    client = create_app(db, scan_lookup=lambda e: {"status": "none", "found": False,
                                                   "findings": [], "fresh": False}).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    source_rid = client.post(f"/author/{tid}/row", json={
        "layer": 1, "head": "H", "remedy": "Nerve Pulse"}).get_json()["rid"]
    client.post(f"/author/{tid}/row", json={
        "layer": 2, "head": "H2", "remedy": "Calm Current"})
    with sqlite3.connect(db) as cx:
        st.add_stress(cx, tid, "Loose ends", source="scan", balance="required")
        sid = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()[0]
        st.cover_stress(cx, tid, sid, [source_rid])
    res = client.post(f"/author/{tid}/layer/1/consolidate-balances",
                      json={"target_layer": 2})
    assert res.status_code == 200 and res.get_json()["moved"] == 1
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT remedy FROM biofield_auth_remedy_coverage").fetchall() == [
            ("calm current",)]
