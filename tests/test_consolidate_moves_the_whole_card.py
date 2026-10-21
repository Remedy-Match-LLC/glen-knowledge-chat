"""Consolidating a layer card moves every row on the card (Glen, 2026-10-20).

A card on the page groups rows by Head (`group_layers`). The Minimum program writes
each remedy on its own stored layer number, so one Glaucoma card held row 456 on
layer 12 and row 457 on layer 13. Consolidating it selected the source by `layer=12`
and left row 457 behind on a leftover Glaucoma card.

The same gap was in "add balanced stress to layer N" on a card: N is the card's
display position, and the route read it as a stored layer number.
"""
import sqlite3

import pytest

from dashboard import biofield_stress as st
from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables

GLAUCOMA = "Suspect of Glaucoma/high pressures and thinning cornea, Current, 2020"


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)


def _chain(cx, tid, rows):
    for layer, head, remedy in rows:
        add_chain_row(cx, tid, layer, head, "", remedy)
    return [r[0] for r in cx.execute(
        "SELECT id FROM biofield_auth_chain WHERE test_id=? ORDER BY id",
        (st._num(tid),)).fetchall()]


def _sharon_like(db):
    """Eye card on layer 6; one Glaucoma card whose two rows sit on layers 12 and 13."""
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-10-20")
        eye, ocu, iop = _chain(cx, tid, [(6, "Eye and Vision Care", "Eye Support"),
                                         (12, GLAUCOMA, "OcuFlow Bedtime"),
                                         (13, GLAUCOMA, "IOP Syntropy")])
        st.add_stress(cx, tid, "Intraocular pressure", source="scan", balance="required")
        sid = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()[0]
        st.cover_stress(cx, tid, sid, [iop])
    return tid, eye, ocu, iop


def _rows(db):
    with sqlite3.connect(db) as cx:
        return {r[0]: (r[1], r[2]) for r in cx.execute(
            "SELECT id, layer, head FROM biofield_auth_chain")}


def test_the_page_shows_one_card_holding_both_rows(tmp_path):
    from dashboard.biofield_authoring import ordered_chain
    from dashboard.biofield_report_html import group_layers
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        groups = group_layers(ordered_chain(cx, tid))
    assert [[r["id"] for r in g["rows"]] for g in groups] == [[eye], [ocu, iop]]


def test_a_card_on_layers_12_and_13_consolidates_both_rows(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=[ocu, iop],
                                      target_rids=[eye])
    rows = _rows(db)
    assert rows[ocu] == (6, "Eye and Vision Care")
    assert rows[iop] == (6, "Eye and Vision Care"), "REGRESSION: the layer-13 row stayed behind"


def test_a_layer_number_alone_stands_for_its_whole_card(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 12, 6)
    rows = _rows(db)
    assert rows[ocu][0] == 6 and rows[iop][0] == 6


def test_a_destination_card_on_two_layers_receives_on_every_row(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-10-20")
        ocu, iop, liver = _chain(cx, tid, [(12, GLAUCOMA, "OcuFlow Bedtime"),
                                           (13, GLAUCOMA, "IOP Syntropy"),
                                           (14, "Liver", "Liver Support")])
        st.consolidate_layer_balances(cx, tid, 14, 12)
        placed = {r[0] for r in cx.execute(
            "SELECT DISTINCT chain_rid FROM biofield_auth_layer_stress")}
    assert placed == {ocu, iop}, "REGRESSION: only the layer-12 row of the destination"
    assert _rows(db)[liver][0] == 12


def test_foreign_row_ids_are_refused_and_nothing_moves(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        other = create_test(cx, "Other", "other@example.com", "2026-10-20")
        add_chain_row(cx, other, 1, "Other head", "", "Other remedy")
        foreign = cx.execute("SELECT id FROM biofield_auth_chain WHERE test_id=?",
                             (st._num(other),)).fetchone()[0]
    before = _rows(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="not on this intake"):
            st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=[ocu, foreign],
                                          target_rids=[eye])
        with pytest.raises(ValueError, match="not on this intake"):
            st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=[ocu, iop],
                                          target_rids=[foreign])
    assert _rows(db) == before


def test_source_and_target_cards_must_differ_by_rows(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="must differ"):
            st.consolidate_layer_balances(cx, tid, 12, 13)


def _client(db):
    from biofield_local_app import create_app
    return create_app(db, fetch_profile=lambda e: {},
                      fetch_recent_comms=lambda e: {}).test_client()


def test_route_moves_every_row_the_card_sends(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    res = _client(db).post(f"/author/{tid}/layer/12/consolidate-balances", json={
        "target_layer": 6, "source_rids": [ocu, iop], "target_rids": [eye]})
    assert res.status_code == 200 and res.get_json()["ok"] is True
    rows = _rows(db)
    assert rows[ocu][0] == 6 and rows[iop][0] == 6


def test_route_refuses_a_foreign_row(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        other = create_test(cx, "Other", "other@example.com", "2026-10-20")
        add_chain_row(cx, other, 1, "Other head", "", "Other remedy")
        foreign = cx.execute("SELECT id FROM biofield_auth_chain WHERE test_id=?",
                             (st._num(other),)).fetchone()[0]
    before = _rows(db)
    res = _client(db).post(f"/author/{tid}/layer/12/consolidate-balances", json={
        "target_layer": 6, "source_rids": [ocu, iop, foreign], "target_rids": [eye]})
    assert res.status_code == 400 and res.get_json()["ok"] is False
    assert _rows(db) == before


def test_the_page_sends_both_cards_row_ids(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    html = _client(db).get(f"/author/{tid}").get_data(as_text=True)
    assert f"data-rids='{ocu},{iop}' value='12'" in html     # destination option
    assert "source_rids:rids(card&&card.dataset.rids)" in html
    assert "target_rids:rids(opt&&opt.getAttribute('data-rids'))" in html


def test_add_stress_on_a_card_uses_the_cards_rows_not_its_display_number(tmp_path):
    """Card "Liver" shows as layer 2 but its row is stored on layer 3. Stored layer 2
    is the second row of the Gut card. The number alone put the stress on Gut."""
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-10-20")
        gut1, gut2, liver = _chain(cx, tid, [(1, "Gut", "Microbiome"), (2, "Gut", "Terrain"),
                                             (3, "Liver", "Liver Support")])
    client = _client(db)
    html = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "addLayerStress(this,2)" in html                   # the card's display number
    assert "addStress(label,layer,rids)" in html
    assert "if(rids&&rids.length)body.rids=rids" in html
    j = client.post(f"/author/{tid}/stress/add",
                    json={"label": "Bile flow", "layer": 3, "rids": [liver]}).get_json()
    assert j["ok"] is True
    with sqlite3.connect(db) as cx:
        placed = {r[0] for r in cx.execute(
            "SELECT chain_rid FROM biofield_auth_layer_stress WHERE stress_id=?",
            (j["sid"],))}
    assert placed == {liver}, "REGRESSION: the display number was read as a stored layer"


def test_add_stress_refuses_a_foreign_row(tmp_path):
    db = str(tmp_path / "c.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-10-20")
        other = create_test(cx, "Other", "other@example.com", "2026-10-20")
        _chain(cx, tid, [(1, "Gut", "Microbiome")])
        add_chain_row(cx, other, 1, "Other head", "", "Other remedy")
        foreign = cx.execute("SELECT id FROM biofield_auth_chain WHERE test_id=?",
                             (st._num(other),)).fetchone()[0]
    res = _client(db).post(f"/author/{tid}/stress/add",
                           json={"label": "Bloating", "layer": 1, "rids": [foreign]})
    assert res.status_code == 400 and res.get_json()["ok"] is False
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0


# Review rounds 1 and 2, 2026-10-20.

def _links(db):
    with sqlite3.connect(db) as cx:
        return {r[0] for r in cx.execute(
            "SELECT DISTINCT chain_rid FROM biofield_auth_layer_stress")}


def test_a_stale_source_moves_a_row_added_to_the_card_since_the_page_loaded(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:                  # another tab adds to the card
        add_chain_row(cx, tid, 14, GLAUCOMA, "", "Eye Drops")
        late = cx.execute("SELECT MAX(id) FROM biofield_auth_chain").fetchone()[0]
        st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=[ocu, iop],
                                      target_rids=[eye])
    rows = _rows(db)
    assert rows[late] == (6, "Eye and Vision Care"), "REGRESSION: the late row stayed behind"


def test_a_stale_destination_includes_the_rows_of_a_card_it_absorbed(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:                  # another tab merged a card into Eye
        add_chain_row(cx, tid, 7, "Eye and Vision Care", "", "Lens Support")
        absorbed = cx.execute("SELECT MAX(id) FROM biofield_auth_chain").fetchone()[0]
        st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=[ocu, iop],
                                      target_rids=[eye])
    assert absorbed in _links(db), "REGRESSION: the absorbed row got no stress links"


def test_rows_now_on_two_cards_are_refused_and_nothing_changes(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        liver = _chain(cx, tid, [(20, "Liver", "Liver Support")])[-1]
    before, links = _rows(db), _links(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="now on more than one layer"):
            st.consolidate_layer_balances(cx, tid, 12, 20, source_rids=[ocu, eye],
                                          target_rids=[liver])
    assert _rows(db) == before and _links(db) == links


def test_rows_with_none_on_the_stated_layer_are_refused(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    before = _rows(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="changed since the page loaded"):
            st.consolidate_layer_balances(cx, tid, 5, 6, source_rids=[ocu, iop],
                                          target_rids=[eye])
        with pytest.raises(ValueError, match="changed since the page loaded"):
            st.consolidate_layer_balances(cx, tid, 12, 9, source_rids=[ocu, iop],
                                          target_rids=[eye])
    assert _rows(db) == before


def _two_heads_on_one_number(db):
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        st.init_stress_tables(cx)
        tid = create_test(cx, "Fixture", "fixture@example.com", "2026-10-20")
        a, b = _chain(cx, tid, [(3, "Adrenal", "Adrenal Syntropy"), (3, "Bone", "Bone Support")])
    return tid, a, b


def test_two_cards_that_share_a_layer_number_can_consolidate(tmp_path):
    db = str(tmp_path / "c.db")
    tid, a, b = _two_heads_on_one_number(db)
    with sqlite3.connect(db) as cx:
        st.consolidate_layer_balances(cx, tid, 3, 3, source_rids=[a], target_rids=[b])
    assert _rows(db) == {a: (3, "Bone"), b: (3, "Bone")}


def test_the_same_card_on_both_sides_is_refused(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="must differ"):
            st.consolidate_layer_balances(cx, tid, 12, 13, source_rids=[ocu],
                                          target_rids=[iop])


def test_a_number_shared_by_two_cards_is_refused_without_ids(tmp_path):
    db = str(tmp_path / "c.db")
    tid, a, b = _two_heads_on_one_number(db)
    with sqlite3.connect(db) as cx:
        _chain(cx, tid, [(4, "Colon", "Colon Support")])
        with pytest.raises(ValueError, match="More than one layer card uses layer number 3"):
            st.consolidate_layer_balances(cx, tid, 3, 4)


@pytest.mark.parametrize("bad", ["12", "456", True, 2.0, ["12"], [True], [2.9], [None], {"a": 1}])
def test_ids_must_be_real_integers(tmp_path, bad):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    before = _rows(db)
    with sqlite3.connect(db) as cx:
        with pytest.raises(ValueError, match="invalid row id"):
            st.consolidate_layer_balances(cx, tid, 12, 6, source_rids=bad, target_rids=[eye])
    assert _rows(db) == before


@pytest.mark.parametrize("bad", ["12", True, False, 0, [str(1)]])
def test_routes_refuse_ids_that_are_not_integers(tmp_path, bad):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    client = _client(db)
    res = client.post(f"/author/{tid}/layer/12/consolidate-balances", json={
        "target_layer": 6, "source_rids": bad, "target_rids": [eye]})
    assert res.status_code == 400 and res.get_json()["error"] == "invalid row id"
    res = client.post(f"/author/{tid}/stress/add",
                      json={"label": "Bloating", "layer": 1, "rids": bad})
    assert res.status_code == 400 and res.get_json()["error"] == "invalid row id"


def test_route_target_ids_name_a_destination_on_two_layers(tmp_path):
    """The destination card spans 12 and 13, and another card also uses 12. The
    number alone is ambiguous, so only the target ids can name the card."""
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        _chain(cx, tid, [(12, "Other on twelve", "Other Remedy")])
    res = _client(db).post(f"/author/{tid}/layer/6/consolidate-balances", json={
        "target_layer": 12, "source_rids": [eye], "target_rids": [ocu, iop]})
    assert res.status_code == 200, res.get_json()
    assert {ocu, iop} <= _links(db)
    assert _rows(db)[eye] == (12, GLAUCOMA)


def test_the_number_only_path_also_moves_wholly_empty_rows(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 12, "", "", "")
        empty = cx.execute("SELECT MAX(id) FROM biofield_auth_chain").fetchone()[0]
        st.consolidate_layer_balances(cx, tid, 12, 6)
    assert _rows(db)[empty][0] == 6


def _js(html, start, end):
    a = html.index(start)
    return html[a:html.index(end, a)]


def _run_node(script):
    import json
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("needs node")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_STUBS = """
var sent=[];var location={reload:function(){sent.push('reload')}};
function astat(){} function loadStress(){} function confirm(){return true}
async function post(p,b){sent.push([p,b]);return {ok:true}}
"""


def test_add_layer_stress_posts_the_cards_rids_as_numbers(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    html = _client(db).get(f"/author/{tid}").get_data(as_text=True)
    fns = _js(html, "async function addStress(", "async function assignStress(")
    got = _run_node(_STUBS + fns + """
var card={dataset:{rids:'456,457'},querySelector:function(q){
 return q==='input[id$="_layer"]'?{value:'12'}:null}};
var input={value:' Bile flow ',disabled:false,closest:function(s){return s==='.lcard'?card:null}};
addLayerStress(input,2).then(function(){console.log(JSON.stringify(sent))});
""")
    # The stored layer from the card's hidden input, never the display number 2.
    assert got[0][1] == {"label": "Bile flow", "layer": 12, "rids": [456, 457]}
    assert got[1] == "reload"


def test_consolidate_posts_both_cards_rids_as_numbers(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    html = _client(db).get(f"/author/{tid}").get_data(as_text=True)
    fn = _js(html, "async function consolidateBalances(", "async function renameStress(")
    got = _run_node(_STUBS + fn + """
var card={dataset:{rids:'456,457'}};
var opt={text:'Layer 1: Eye',getAttribute:function(k){return k==='data-rids'?'5':null}};
var sel={value:'6',selectedIndex:0,options:[opt],closest:function(s){return s==='.lcard'?card:null}};
consolidateBalances(12,2,sel).then(function(){console.log(JSON.stringify(sent))});
""")
    assert got[0][1] == {"target_layer": 6, "source_rids": [456, 457], "target_rids": [5]}


# Add stress on a card resolves to the current card too (coordinator, 2026-10-20).

def _stress_links(db, sid):
    with sqlite3.connect(db) as cx:
        return {r[0] for r in cx.execute(
            "SELECT chain_rid FROM biofield_auth_layer_stress WHERE stress_id=?", (sid,))}


def _stress_count(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0]


def test_a_stale_add_stress_covers_a_row_added_to_the_card_since_load(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    with sqlite3.connect(db) as cx:                  # another tab adds to the card
        add_chain_row(cx, tid, 14, GLAUCOMA, "", "Eye Drops")
        late = cx.execute("SELECT MAX(id) FROM biofield_auth_chain").fetchone()[0]
    j = _client(db).post(f"/author/{tid}/stress/add", json={
        "label": "Corneal thinning", "layer": 12, "rids": [ocu, iop]}).get_json()
    assert j["ok"] is True
    assert _stress_links(db, j["sid"]) == {ocu, iop, late}, (
        "REGRESSION: the stress covered only part of the card")


def test_add_stress_refuses_rows_now_on_two_cards(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    before = _stress_count(db)
    res = _client(db).post(f"/author/{tid}/stress/add", json={
        "label": "Corneal thinning", "layer": 12, "rids": [ocu, eye]})
    assert res.status_code == 400
    assert res.get_json()["error"].startswith("Those rows are now on more than one layer.")
    assert _stress_count(db) == before


def test_add_stress_refuses_rows_with_none_on_the_stated_layer(tmp_path):
    db = str(tmp_path / "c.db")
    tid, eye, ocu, iop = _sharon_like(db)
    before = _stress_count(db)
    res = _client(db).post(f"/author/{tid}/stress/add", json={
        "label": "Corneal thinning", "layer": 2, "rids": [ocu, iop]})
    assert res.status_code == 400
    assert res.get_json()["error"].startswith("That layer has changed since the page loaded.")
    assert _stress_count(db) == before
