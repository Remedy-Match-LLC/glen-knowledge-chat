"""The BSI location (Glen, 15 November 2026).

"The 4 numbers, and the location ... after the first 2 numbers." "Terrain location is
correctly placed after the terrain phase. BSI location is separate." The BSI row reads
"A x B [BSI location] to C x D". Spoken, either "BSI 21 times 3 to 1 times 1, location
Liver" or "21 x 3 Liver to 1 x 1", and "The score is" is the same cue as "BSI".
The rules are written above segment_utterance in dashboard/biofield_layer_match.py,
rules 14 and 15.
"""
import json
import re
import sqlite3

import pytest

from dashboard.biofield_authoring import (authored_report, confirm_all, get_terrain_review,
                                          init_auth_tables, set_terrain_bsi,
                                          set_terrain_field, terrain_state, update_terrain)
from dashboard.biofield_interpret import interpret_transcript
from dashboard.biofield_layer_match import handsfree_events, parse_bsi_parts, segment_utterance
from tests.test_layer_handsfree_terrain import (DUMP, _app, _run, _terrain_posts,  # noqa: F401
                                                needs_node)
from tests.test_biofield_local_app import _species_known, _no_console_gate  # noqa: F401


def ev(text, **kw):
    return segment_utterance(text, **kw)["events"]


BSI_21 = {"terrain": "bsi", "text": "21 times 3 to 1 times 1", "value": [21, 3, 1, 1],
          "error": "", "warnings": []}


def bsi_loc(text, ok=True, **kw):
    return {"terrain": "location", "text": text, "of": "bsi", "bsi_ok": ok, **kw}


# ---- the stored field -----------------------------------------------------------------

@pytest.fixture
def cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    cx.execute("INSERT INTO biofield_auth_tests(id) VALUES(1)")
    cx.execute("CREATE TABLE fmp_snap_client_active_main_stress(id_pk TEXT, main_stress TEXT)")
    cx.executemany("INSERT INTO fmp_snap_client_active_main_stress VALUES(?,?)",
                   [("1", "Liver Congestion"), ("2", "Kidney Stress")])
    cx.commit()
    return cx


def test_the_column_exists_and_saves_and_reads_back(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(biofield_auth_tests)")}
    assert "bsi_location" in cols
    set_terrain_field(cx, 1, "location", "Kidney Stress")
    assert set_terrain_field(cx, 1, "bsi_location", " Liver ") == "Liver"
    rep = authored_report(cx, 1)
    assert rep["bsi_location"] == "Liver" and rep["location"] == "Kidney Stress"
    assert terrain_state(rep)["bsi_location"] == "Liver"
    set_terrain_field(cx, 1, "bsi_location", "")              # blank clears it
    assert authored_report(cx, 1)["bsi_location"] == ""
    assert authored_report(cx, 1)["location"] == "Kidney Stress"   # the other one stays


def test_hands_free_marks_it_and_a_hand_edit_or_confirm_clears_it(cx):
    set_terrain_field(cx, 1, "bsi_location", "Zzqx", handsfree=True, matched=False)
    assert get_terrain_review(cx, 1) == {"bsi_location": "unmatched"}
    set_terrain_field(cx, 1, "bsi_location", "Liver")          # Glen types over it
    assert get_terrain_review(cx, 1) == {}
    set_terrain_field(cx, 1, "bsi_location", "Liver", handsfree=True, matched=True)
    assert get_terrain_review(cx, 1) == {"bsi_location": "review"}
    confirm_all(cx, 1)
    assert get_terrain_review(cx, 1) == {}


def test_regulation_still_comes_from_the_four_numbers(cx):
    set_terrain_bsi(cx, 1, [13, 8, 5, 3])
    before = terrain_state(authored_report(cx, 1))["regulation"]
    set_terrain_field(cx, 1, "bsi_location", "Liver")
    assert terrain_state(authored_report(cx, 1))["regulation"] == before == {
        "level": 4, "name": "Positive"}


def test_update_terrain_saves_the_bsi_location_only_with_a_whole_bsi(cx):
    update_terrain(cx, 1, bsi=[12, 8, None, None], bsi_location="Liver")
    assert authored_report(cx, 1)["bsi_location"] == ""
    update_terrain(cx, 1, bsi=[12, 8, 9, 6], bsi_location="Liver")
    assert authored_report(cx, 1)["bsi_location"] == "Liver"
    update_terrain(cx, 1, phase=3)                              # a pass that heard none
    assert authored_report(cx, 1)["bsi_location"] == "Liver"


def test_the_interpreter_reads_a_bsi_location_apart_from_the_terrain_location():
    reply = json.dumps({"header": "", "phase": 3, "location": "kidney",
                        "bsi": [21, 3, 1, 1], "bsi_location": " Liver ", "layers": []})
    out = interpret_transcript("BSI 21 times 3 to 1 times 1, location Liver.",
                               lambda system, user: reply)
    assert out["bsi_location"] == "Liver" and out["location"] == "kidney"
    assert interpret_transcript("", None)["bsi_location"] == ""


def test_the_route_saves_it_for_review(tmp_path):
    db, client, tid = _app(tmp_path)
    j = client.post(f"/author/{tid}/terrain", json={
        "field": "bsi_location", "value": "Zzqx", "matched": False,
        "handsfree": True}).get_json()
    assert j["ok"] and j["bsi_location"] == "Zzqx" and j["location"] == ""
    assert j["review"] == {"bsi_location": "unmatched"}
    with sqlite3.connect(db) as cx:
        assert authored_report(cx, tid)["bsi_location"] == "Zzqx"


# ---- the page -----------------------------------------------------------------------

def test_the_input_sits_after_b_and_before_to(tmp_path):
    db, client, tid = _app(tmp_path)
    client.post(f"/author/{tid}/terrain", json={"field": "bsi_location", "value": "Liver"})
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    seg = h[h.index("<div id=terrainSegs"):]
    m = re.search(r"<input id=bsi_location [^>]*>", seg)
    assert m, "no BSI location input"
    tag = m.group(0)
    assert " list=vocab " in tag and "value=\"Liver\"" in tag
    assert "onchange=\"saveTerrain('bsi_location',this)\"" in tag
    # The row reads "A x B [BSI location] to C x D".
    order = [seg.index(x) for x in ("<input id=bsi_a ", "<input id=bsi_b ",
                                    "<input id=bsi_location ", "<span>to</span>",
                                    "<input id=bsi_c ", "<input id=bsi_d ")]
    assert order == sorted(order)
    # The terrain location stays where it was: after the phase, in the row above.
    assert seg.index("<select id=t_phase") < seg.index("<input id=t_location ") \
        < seg.index("Biological Score Index") < seg.index("<input id=bsi_location ")
    st = json.loads(re.search(r"<script>window._tStored=(\{.*?\})</script>", seg).group(1))
    assert st["bsi_location"] == "Liver"


def test_the_bsi_row_was_not_a_then_b_then_to_any_more(tmp_path):
    """Inverted from the old layout: B was followed straight by "to"."""
    db, client, tid = _app(tmp_path)
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert not re.search(r"onchange=\"saveTerrain\('bsi_b',this\)\"> <span>to</span>", h)


# ---- the segmenter: the two spoken forms and the score cue ----------------------------

def test_glens_sentence_location_after_the_numbers():
    assert ev("BSI 21 times 3 to 1 times 1, location Liver.") == [BSI_21, bsi_loc("Liver")]


def test_glens_sentence_location_between_the_numbers():
    (b, loc) = ev("BSI 21 x 3 Liver to 1 x 1")
    assert b["value"] == [21, 3, 1, 1] and b["error"] == ""
    assert loc == bsi_loc("Liver")


@pytest.mark.parametrize("words,loc", [
    ("12 by 8 liver to 9 by 6", "liver"), ("12 by 8 at the liver to 9 by 6", "liver"),
    ("12 by 8 in liver to 9 by 6", "liver"),
    ("12 by 8 liver and kidney to 9 by 6", "liver and kidney"),
    ("12 by 8 to 9 by 6", ""), ("12, 8, 9, 6", ""),
])
def test_parse_bsi_parts_reads_words_between_the_second_and_third(words, loc):
    nums, got, why = parse_bsi_parts(words)
    assert nums == [12, 8, 9, 6] and got == loc and why == ""


@pytest.mark.parametrize("words", ["12 liver by 8 to 9 by 6", "12 by 8 to 9 liver by 6",
                                   "12 by 8 to 9 by 6 liver", "liver 12 by 8 to 9 by 6"])
def test_words_anywhere_else_still_mean_not_understood(words):
    assert parse_bsi_parts(words)[0] is None


def test_both_forms_the_words_between_win_and_the_other_is_kept_as_conflict():
    (b, loc) = ev("BSI 21 x 3 Liver to 1 x 1, location kidney.")
    assert b["value"] == [21, 3, 1, 1]
    assert loc == bsi_loc("Liver", conflict="kidney")


@pytest.mark.parametrize("said", ["The score is 21 times 3 to 1 times 1, location Liver",
                                  "the score 21 times 3 to 1 times 1, location Liver",
                                  "Score is 21 times 3 to 1 times 1, location Liver",
                                  "So the score is 21 times 3 to 1 times 1, location Liver"])
def test_the_score_is_the_bsi_cue(said):
    assert ev(said) == [BSI_21, bsi_loc("Liver")]


def test_the_score_with_the_location_between():
    (b, loc) = ev("the score is 21 x 3 liver to 1 x 1")
    assert b["terrain"] == "bsi" and b["value"] == [21, 3, 1, 1]
    assert loc == bsi_loc("liver")


@pytest.mark.parametrize("said", ["The BSI is 21 x 3 liver to 1 x 1",
                                  "BSI is 21 x 3 liver to 1 x 1",
                                  "the B.S.I. is 21 x 3 liver to 1 x 1",
                                  "The BSI 21 x 3 liver to 1 x 1"])
def test_the_bsi_is_starts_the_bsi(said):
    (b, loc) = ev(said)
    assert b["terrain"] == "bsi" and b["value"] == [21, 3, 1, 1] and b["error"] == ""
    assert loc == bsi_loc("liver")


def test_the_bsi_is_with_the_location_after():
    assert ev("The BSI is 21 times 3 to 1 times 1, location Liver.") == [
        BSI_21, bsi_loc("Liver")]


def test_score_inside_other_words_is_not_a_cue():
    assert ev("Remedy Scoreplex.") == [{"field": "remedy", "text": "Scoreplex", "both": False}]
    assert ev("Head, the score is high.") == [
        {"field": "head", "text": "the score is high", "both": False}]
    assert ev("Her score is better today, 21 by 3 to 1 by 1.") == []
    assert ev("21 x 3 Liver to 1 x 1") == []              # numbers without the cue


def test_terrain_then_location_fills_the_terrain_location():
    assert ev("Terrain 3, location liver") == [
        {"terrain": "phase", "text": "3", "value": 3, "name": "Regenerate"},
        {"terrain": "location", "text": "liver"}]
    assert ev("Terrain 3. Location liver.")[1] == {"terrain": "location", "text": "liver"}


def test_a_location_on_its_own_fills_the_terrain_location():
    assert ev("Location liver.") == [{"terrain": "location", "text": "liver"}]


def test_a_bad_bsi_then_a_location_saves_neither():
    (b, loc) = ev("BSI 22 by 3 to 1 by 1, location Liver.")
    assert b["value"] is None and b["error"] == "22 is outside 1 to 21"
    assert loc == bsi_loc("Liver", ok=False)
    (b, loc) = ev("BSI 21 by 3 to 1. Location Liver.")
    assert b["value"] is None and loc == bsi_loc("Liver", ok=False)
    # Words between the numbers of a bad BSI make no location at all.
    assert [e["terrain"] for e in ev("BSI 22 x 3 Liver to 1 x 1")] == ["bsi"]


def test_the_next_utterance_still_belongs_to_the_bsi():
    out = segment_utterance("BSI 21 times 3 to 1 times 1.")
    assert out["after_bsi"] == "ok"
    assert ev("Location Liver.", after_bsi=out["after_bsi"]) == [bsi_loc("Liver")]
    out = segment_utterance("BSI 21 times 3 to 1 times 1. Location.")
    assert out["pending"] == "location" and out["after_bsi"] == "ok"
    assert ev("Liver", pending="location", after_bsi="ok") == [bsi_loc("Liver")]
    assert segment_utterance("BSI 22 by 3 to 1 by 1.")["after_bsi"] == "bad"


def test_anything_said_between_ends_it():
    evs = ev("BSI 21 times 3 to 1 times 1. Head liver. Location gut.")
    assert evs[-1] == {"terrain": "location", "text": "gut"}
    assert ev("Location gut.", after_bsi=None) == [{"terrain": "location", "text": "gut"}]
    assert "after_bsi" not in segment_utterance("BSI 21 by 3 to 1 by 1. How is that?")
    assert ev("Okay. Location gut.", after_bsi="ok") == [bsi_loc("gut")]   # filler only


def test_the_bsi_location_is_matched_against_the_stress_list(cx):
    hit, = handsfree_events(cx, "Location liver congestion.", after_bsi="ok")["events"]
    assert hit["of"] == "bsi" and hit["match"] == {**hit["match"], "value": "Liver Congestion",
                                                   "matched": True}
    _b, miss = handsfree_events(cx, "BSI 21 x 3 zzqx florp to 1 x 1")["events"]
    assert miss["of"] == "bsi" and miss["match"]["matched"] is False
    assert miss["match"]["value"] == "Zzqx Florp"


def test_the_segment_route_carries_after_bsi(tmp_path):
    db, client, tid = _app(tmp_path)
    url = f"/author/{tid}/handsfree-segment"
    j = client.post(url, json={"text": "BSI 21 by 3 to 1 by 1."}).get_json()
    assert j["after_bsi"] == "ok"
    j = client.post(url, json={"text": "Location liver.", "after_bsi": "ok"}).get_json()
    assert j["events"][0]["of"] == "bsi" and "after_bsi" not in j


# ---- the page JS under node -----------------------------------------------------------

BEFORE = "els.bsi_location=El('bsi_location','');"
DUMP_LOC = DUMP.replace("d:els.bsi_d.value,", "d:els.bsi_d.value,bl:els.bsi_location.value,") \
    .replace("a:els.bsi_a._cls}", "a:els.bsi_a._cls,bl:els.bsi_location._cls}")


@needs_node
def test_js_saves_a_matched_bsi_location_for_review():
    r = _run("""
      answers['u']=seg([{terrain:'bsi',text:'21 x 3 liver to 1 x 1',value:[21,3,1,1],error:'',
        warnings:[]},{terrain:'location',text:'liver',of:'bsi',bsi_ok:true,
        match:{value:'Liver Congestion',matched:true}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP_LOC, before=BEFORE)
    assert _terrain_posts(r) == [
        ("/author/a7/terrain", {"field": "bsi", "value": [21, 3, 1, 1], "handsfree": True}),
        ("/author/a7/terrain", {"field": "bsi_location", "value": "Liver Congestion",
                                "matched": True, "handsfree": True})]
    assert r["vals"]["bl"] == "Liver Congestion" and r["vals"]["loc"] == ""
    assert r["cls"]["bl"] == {"tunconf": True}
    assert "BSI location: Liver Congestion" in r["log"]


@needs_node
def test_js_keeps_an_unmatched_bsi_location_marked():
    r = _run("""
      answers['u']=seg([{terrain:'location',text:'zzqx',of:'bsi',bsi_ok:true,
        match:{value:'Zzqx',matched:false}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP_LOC, before=BEFORE)
    assert _terrain_posts(r) == [("/author/a7/terrain", {
        "field": "bsi_location", "value": "Zzqx", "matched": False, "handsfree": True})]
    assert r["cls"]["bl"] == {"tunconf": True, "unmatched": True}
    assert "BSI location: Zzqx (not matched)" in r["log"]


@needs_node
def test_js_a_location_after_a_bad_bsi_saves_nothing():
    r = _run("""
      answers['u']=seg([{terrain:'bsi',text:'22 by 3 to 1 by 1',value:null,
        error:'22 is outside 1 to 21',warnings:[]},
        {terrain:'location',text:'Liver',of:'bsi',bsi_ok:false,match:{value:'Liver',matched:true}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP_LOC, before=BEFORE)
    assert _terrain_posts(r) == []
    assert "BSI location not saved: Liver. The BSI before it was not understood." in r["log"]
    assert r["vals"]["bl"] == "" and r["vals"]["loc"] == ""


@needs_node
def test_js_logs_a_bsi_location_said_both_ways():
    r = _run("""
      answers['u']=seg([{terrain:'location',text:'Liver',of:'bsi',bsi_ok:true,conflict:'kidney',
        match:{value:'Liver',matched:true}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP_LOC, before=BEFORE)
    assert "BSI location said twice: kept Liver, not kidney." in r["log"]
    assert [b["value"] for _u, b in _terrain_posts(r)] == ["Liver"]


@needs_node
def test_js_carries_after_bsi_to_the_next_utterance():
    r = _run("""
      answers['u1']={ok:true,events:[],pending:null,after_remedy:false,after_bsi:'ok'};
      answers['u2']=seg([]);
      say(me.ws,'u1');await me.chain;say(me.ws,'u2');await me.chain;
    """ + DUMP_LOC, before=BEFORE)
    segs = [p["body"] for p in r["calls"]["post"] if "/handsfree-segment" in p["url"]]
    assert segs == [{"text": "u1", "pending": None, "after_remedy": False},
                    {"text": "u2", "pending": None, "after_remedy": False, "after_bsi": "ok"}]
