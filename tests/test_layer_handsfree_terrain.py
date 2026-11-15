"""Hands-free terrain cues: "terrain", "location" and "BSI" (Glen, 14 November 2026).

They join the layer cues in the hands-free recording and fill the terrain panel, never a
layer. Every value goes in for review (ruling 7), stored, so the highlight survives the
reload; an unmatched location keeps the spoken words (ruling 4). The rule is written out
above segment_utterance in dashboard/biofield_layer_match.py, rules 9 to 12.

Layers: the segmenter (pure), the server (matching, the route, the stored mark), and the
page JS under node.
"""
import json
import re
import shutil
import sqlite3
import subprocess

import pytest

from biofield_local_app import create_app
from dashboard.biofield_authoring import (authored_report, confirm_all, get_terrain_review,
                                          init_auth_tables, set_terrain_field)
from dashboard.biofield_layer_match import (handsfree_events, parse_bsi, parse_phase,
                                            segment_utterance)
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def ev(text, **kw):
    return segment_utterance(text, **kw)["events"]


# ---- the segmenter: each cue -------------------------------------------------------

@pytest.mark.parametrize("said,n", [
    ("Terrain 3.", 3), ("Terrain three", 3), ("terrain is phase five", 5),
    ("Terrain phase 4, cleanse.", 4), ("Terrain cleanse", 4), ("Terrain Refresh", 4),
    ("terrain revived", 1), ("Terrain energise", 1), ("Terrain rejuvenate", 2),
    ("Terrain Renew", 3), ("Terrain balance", 5),
])
def test_terrain_cue_reads_a_number_word_or_phase_name(said, n):
    (e,) = ev(said)
    assert e["terrain"] == "phase" and e["value"] == n


@pytest.mark.parametrize("said", ["Terrain 6", "Terrain zero", "Terrain 2 cleanse",
                                  "Terrain looks better today"])
def test_terrain_cue_not_understood_saves_nothing(said):
    (e,) = ev(said)
    assert e["terrain"] == "phase" and e["value"] is None


def test_location_cue_carries_the_words():
    assert ev("Location is liver congestion.") == [
        {"terrain": "location", "text": "liver congestion"}]


@pytest.mark.parametrize("said", [
    "BSI 12 by 8 to 9 by 6", "BSI 12 x 8 to 9 x 6.", "bsi 12x8 to 9x6",
    "BSI 12, 8, 9, 6", "BSI 12 times 8 to 9 times 6", "B.S.I. 12 by 8 to 9 by 6",
    "b s i twelve by eight to nine by six", "BSI is 12 by 8 and 9 by 6",
])
def test_bsi_cue_fills_four_numbers(said):
    (e,) = ev(said)
    assert e["terrain"] == "bsi" and e["value"] == [12, 8, 9, 6] and e["error"] == ""


def test_bsi_number_words_up_to_twenty_one():
    assert parse_bsi("twenty one by fifteen to twenty by one")[0] == [21, 15, 20, 1]


@pytest.mark.parametrize("words,why", [
    ("12 by 8 to 9", "heard 3 numbers, not 4"),
    ("22 by 8 to 9 by 6", "22 is outside 1 to 21"),
    ("twenty two by 8 to 9 by 6", "22 is outside 1 to 21"),
    ("0 by 8 to 9 by 6", "0 is outside 1 to 21"),
    ("12 by 8 to 9 by 6 by 4", "heard 5 numbers, not 4"),
    ("12 by eight to nine maybe 6", "'maybe' is not a number"),
])
def test_a_bad_bsi_fills_nothing(words, why):
    assert parse_bsi(words) == (None, why)
    (e,) = ev("BSI " + words)
    assert e["value"] is None and e["error"] == why


def test_bsi_warnings_ride_on_the_event():
    (e,) = ev("BSI 8 by 12 to 9 by 6")
    assert e["value"] == [8, 12, 9, 6] and e["warnings"] == ["B is greater than A"]


def test_a_bare_terrain_cue_waits_for_the_next_sentence():
    out = segment_utterance("BSI.")
    assert out == {"events": [], "pending": "bsi", "after_remedy": False}
    (e,) = ev("12 by 8 to 9 by 6", pending="bsi")
    assert e["value"] == [12, 8, 9, 6]
    (e,) = ev("Terrain. Three.")
    assert e == {"terrain": "phase", "text": "Three", "value": 3, "name": "Regenerate"}


def test_terrain_words_mid_sentence_are_not_cues():
    assert ev("The terrain looks better.") == []
    assert ev("Head, pressure at that location.") == [
        {"field": "head", "text": "pressure at that location", "both": False}]


def test_terrain_cues_mid_session_leave_the_layer_cues_alone():
    """A layer, then terrain, then more of the same layer: no next_layer appears, the
    open field and the remedy's dose run on as before around the terrain cue."""
    assert ev("Head liver. Terrain 3. Tail gut. Remedy Sobopla. BSI 12 by 8 to 9 by 6. "
              "Location kidney. Balanced.") == [
        {"field": "head", "text": "liver", "both": False},
        {"terrain": "phase", "text": "3", "value": 3, "name": "Regenerate"},
        {"field": "tail", "text": "gut", "both": False},
        {"field": "remedy", "text": "Sobopla", "both": False},
        {"terrain": "bsi", "text": "12 by 8 to 9 by 6", "value": [12, 8, 9, 6],
         "error": "", "warnings": []},
        {"terrain": "location", "text": "kidney"},
        {"balanced": True}]


def test_a_terrain_cue_ends_the_remedy_dose():
    out = segment_utterance("Remedy Sobopla. Terrain 2. Two drops daily.")
    assert [e.get("field") or e.get("terrain") for e in out["events"]] == ["remedy", "phase"]
    assert out["after_remedy"] is False


# ---- "also balances" (Glen, 14 November 2026, Bridge) ----------------------------------

@pytest.mark.parametrize("said", ["Also balances liver congestion.",
                                  "It also balances liver congestion",
                                  "also balance, liver congestion"])
def test_also_balances_carries_the_stress(said):
    assert ev(said) == [{"also_balances": True, "text": "liver congestion"}]


def test_also_balances_is_not_the_balanced_cue():
    assert ev("Balanced.") == [{"balanced": True}]
    assert ev("Also balances.") == []                     # waits for the stress instead
    assert segment_utterance("Also balances.")["pending"] == "also_balances"
    assert ev("Liver congestion", pending="also_balances") == [
        {"also_balances": True, "text": "Liver congestion"}]
    assert ev("Remedy Sobopla. Also balances gut. Balanced.") == [
        {"field": "remedy", "text": "Sobopla", "both": False},
        {"also_balances": True, "text": "gut"}, {"balanced": True}]
    assert ev("It balances liver congestion.") == []      # no "also": plain talk


def test_parse_phase_needs_one_answer():
    assert parse_phase("3 regenerate") == 3
    assert parse_phase("") is None


# ---- the server: matching ------------------------------------------------------------

@pytest.fixture
def cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    cx.execute("CREATE TABLE fmp_snap_client_active_main_stress(id_pk TEXT, main_stress TEXT)")
    cx.executemany("INSERT INTO fmp_snap_client_active_main_stress VALUES(?,?)",
                   [("1", "Liver Congestion"), ("2", "Heavy Metal Toxicity")])
    cx.commit()
    return cx


def test_location_is_matched_against_the_stress_list(cx):
    out = handsfree_events(cx, "Location liver congestion. Location zzqx florp.")
    hit, miss = out["events"]
    assert hit["match"]["value"] == "Liver Congestion" and hit["match"]["matched"] is True
    assert miss["match"]["matched"] is False
    assert miss["match"]["value"] == "Zzqx Florp"          # the words are kept


def test_also_balances_is_matched_against_the_stress_list(cx):
    hit, miss = handsfree_events(cx, "Also balances liver congestion. Also balances zzqx florp.")["events"]
    assert hit["match"]["value"] == "Liver Congestion" and hit["match"]["matched"] is True
    assert miss["match"]["matched"] is False


# ---- the server: the stored review mark ---------------------------------------------

def test_a_hands_free_value_is_marked_and_a_hand_edit_clears_it(cx):
    cx.execute("INSERT INTO biofield_auth_tests(id) VALUES(1)")
    set_terrain_field(cx, 1, "phase", "3", handsfree=True)
    set_terrain_field(cx, 1, "location", "Zzqx", handsfree=True, matched=False)
    assert get_terrain_review(cx, 1) == {"phase": "review", "location": "unmatched"}
    set_terrain_field(cx, 1, "phase", "4")                  # Glen edits it by hand
    assert get_terrain_review(cx, 1) == {"location": "unmatched"}
    assert authored_report(cx, 1)["terrain_review"] == {"location": "unmatched"}
    confirm_all(cx, 1)
    assert get_terrain_review(cx, 1) == {}


def _app(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client = create_app(db).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    return db, client, tid


def test_route_saves_bsi_all_at_once_for_review(tmp_path):
    db, client, tid = _app(tmp_path)
    url = f"/author/{tid}/terrain"
    j = client.post(url, json={"field": "bsi", "value": [12, 8, 9, 6],
                               "handsfree": True}).get_json()
    assert j["ok"] and j["bsi"] == [12, 8, 9, 6]
    assert j["regulation"] == {"level": 4, "name": "Positive"}
    assert j["review"] == {f: "review" for f in ("bsi_a", "bsi_b", "bsi_c", "bsi_d")}
    # Three numbers, or one out of range: refused, and nothing changes.
    for bad in ([12, 8, 9], [12, 8, 9, 22], [0, 8, 9, 6], "12 8 9 6"):
        r = client.post(url, json={"field": "bsi", "value": bad, "handsfree": True})
        assert r.status_code == 400, bad
    with sqlite3.connect(db) as cx:
        assert authored_report(cx, tid)["bsi"] == [12, 8, 9, 6]
    # Glen types over C: only C leaves review.
    j = client.post(url, json={"field": "bsi_c", "value": "10"}).get_json()
    assert set(j["review"]) == {"bsi_a", "bsi_b", "bsi_d"}
    j = client.post(f"/author/{tid}/terrain/confirm", json={}).get_json()
    assert j["ok"] and j["review"] == {}


def test_route_keeps_an_unmatched_location_marked(tmp_path):
    db, client, tid = _app(tmp_path)
    j = client.post(f"/author/{tid}/terrain", json={
        "field": "location", "value": "Zzqx Florp", "matched": False,
        "handsfree": True}).get_json()
    assert j["location"] == "Zzqx Florp" and j["review"] == {"location": "unmatched"}


def test_stress_add_route_with_handsfree_sends_the_layer_to_review(tmp_path):
    db = str(tmp_path / "c.db")                     # a fresh db, as the stress route tests use
    client = create_app(db).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    a = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver",
                                                 "remedy": "TMG"}).get_json()["rid"]
    b = client.post(f"/author/{tid}/row", json={"layer": 2, "head": "Lung",
                                                 "remedy": "TMG"}).get_json()["rid"]
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE biofield_auth_chain SET confirmed=1")
        cx.commit()
    j = client.post(f"/author/{tid}/stress/add", json={
        "label": "Liver Congestion", "layer": 1, "rids": [a], "handsfree": True}).get_json()
    assert j["ok"] and j["layer"] == 1
    client.post(f"/author/{tid}/stress/add", json={"label": "Gut", "layer": 2, "rids": [b]})
    with sqlite3.connect(db) as cx:
        got = dict(cx.execute("SELECT id, confirmed FROM biofield_auth_chain").fetchall())
    assert got == {a: 0, b: 1}                 # the card's own box confirms nothing away
    s_ = client.get(f"/author/{tid}/stresses").get_json()["data"]
    assert {"Liver Congestion", "Gut"} <= {x["label"] for x in s_["balanced"]}


def test_page_highlights_stored_review_marks(tmp_path):
    db, client, tid = _app(tmp_path)
    client.post(f"/author/{tid}/terrain", json={"field": "phase", "value": "2",
                                                "handsfree": True})
    client.post(f"/author/{tid}/terrain", json={"field": "location", "value": "Zzqx",
                                                "matched": False, "handsfree": True})
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "<select id=t_phase aria-label='Terrain phase' class='tunconf'" in h
    assert "class='tunconf unmatched'" in h
    assert re.search(r"id=tConfirm [^>]*style=''", h)
    assert ("title='Hands-free: say head, tail, remedy, balanced, also balances, next layer, "
            "terrain, "
            "location or BSI'") in h
    assert "id=bsi_a" in h and "bsi_a type=number min=1 max=21 step=1 inputmode=numeric " \
        "aria-label='BSI A' value='' style='width:56px' onchange" in h


# ---- the page JS under node -----------------------------------------------------------

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed on this machine")


def _blocks(*names):
    from dashboard.biofield_report_html import _AUTHOR_JS
    out = []
    for name in names:
        m = re.search(r"// %s:begin\n(.*?)// %s:end" % (name, name), _AUTHOR_JS, re.S)
        assert m, f"{name} markers missing"
        out.append(m.group(1))
    return "\n".join(out).replace("__TID__", "a7")


HARNESS = r"""
var window=globalThis;
function El(id,v){var e={id:id,value:v||'',textContent:'',title:'',checked:false,dataset:{},
 style:{},_cls:{},focus:function(){},scrollIntoView:function(){}};
 e.classList={add:function(c){e._cls[c]=true},remove:function(c){delete e._cls[c]},
  contains:function(c){return !!e._cls[c]}};return e}
var els={};
['g1_head','g1_most','g1_layer','g1_balanced','sessText','astat','hfStat','hfLog','hfBtn',
 't_phase','t_location','bsi_a','bsi_b','bsi_c','bsi_d','t_regulation','t_warn','tstat',
 'tConfirm'].forEach(function(id){els[id]=El(id,'')});
els.g1_layer.value='4';els.g1_head.value='Kidney';els.g1_most.value='Bladder';
var cards=[{dataset:{gid:'g1',rids:'7'}}];
var document={activeElement:null,getElementById:function(id){return els[id]||null},
 addEventListener:function(){},querySelector:function(){return null},
 querySelectorAll:function(sel){return sel.indexOf('data-rids')>=0?cards:[]}};
function val(id){var e=document.getElementById(id);return e?e.value:''}
var calls={post:[],saveNow:0,reloads:0};var _sess='',_dg=null;
function scheduleSessionSave(){}
async function saveSessionNow(){calls.saveNow++}
function reloadKeepingView(){calls.reloads++}
var answers={},stored={phase:null,location:'',bsi:[null,null,null,null],review:{},warnings:[]};
async function post(url,body){calls.post.push({url:url,body:JSON.parse(JSON.stringify(body))});
 if(url.indexOf('/handsfree-segment')>=0)return answers[body.text]||{ok:true,events:[]};
 if(/\/terrain$/.test(url)){
  if(body.field==='bsi'){stored.bsi=body.value.slice();['bsi_a','bsi_b','bsi_c','bsi_d']
   .forEach(function(f){stored.review[f]='review'})}
  else{stored[body.field]=body.value;stored.review[body.field]=
   body.handsfree?(body.matched===false?'unmatched':'review'):undefined;
   if(!body.handsfree)delete stored.review[body.field]}
  return Object.assign({ok:true},JSON.parse(JSON.stringify(stored)))}
 if(/\/terrain\/confirm$/.test(url)){stored.review={};
  return Object.assign({ok:true},JSON.parse(JSON.stringify(stored)))}
 if(/\/row$/.test(url))return {ok:true,rid:body.anchor_rid||100};
 return {ok:true}}
async function fetch(u){return {json:async function(){return {key:'k'}}}}
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{
 getUserMedia:async function(){return {getTracks:function(){return [{stop:function(){}}]}}}}}});
function MediaRecorder(s,o){this.state='inactive'}
MediaRecorder.prototype.start=function(){this.state='recording'};
MediaRecorder.prototype.stop=function(){this.state='inactive'};
MediaRecorder.isTypeSupported=function(){return true};window.MediaRecorder=MediaRecorder;
function WebSocket(url,proto){this.readyState=1;this.sent=[]}
WebSocket.prototype.send=function(d){this.sent.push(d)};
WebSocket.prototype.close=function(){this.readyState=3};
function say(ws,text){ws.onmessage({data:JSON.stringify({type:'Results',is_final:true,
 speech_final:true,channel:{alternatives:[{transcript:text}]}})})}
function seg(evs){return {ok:true,events:evs,pending:null,after_remedy:false}}
function H(t,v){return {field:'head',text:t,both:false,match:{value:v,matched:true}}}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,log:els.hfLog.textContent,"
        "tstat:els.tstat.textContent,sess:els.sessText.value,"
        "vals:{phase:els.t_phase.value,loc:els.t_location.value,a:els.bsi_a.value,"
        "d:els.bsi_d.value,reg:els.t_regulation.textContent},"
        "cls:{phase:els.t_phase._cls,loc:els.t_location._cls,a:els.bsi_a._cls},"
        "confirm:els.tConfirm.style.display,target:_hf&&_hf.target}));")


def _run(scenario, before=""):
    script = (HARNESS + _blocks("recField", "handsFree", "regulationLevel", "terrainSave")
              + "\nHF_DRAIN_MS=10;HF_FLUSH_MS=0;\n(async function(){\n" + before +
              "var me=await handsFree();me.ws.onopen();\n" + scenario + "\n})()"
              ".catch(function(e){console.error(e);process.exit(1)});\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _terrain_posts(r):
    return [(p["url"], p["body"]) for p in r["calls"]["post"] if "/terrain" in p["url"]]


@needs_node
def test_js_each_terrain_cue_saves_for_review_and_highlights():
    r = _run("""
      answers['u']=seg([{terrain:'phase',text:'3',value:3,name:'Regenerate'},
        {terrain:'location',text:'zzqx',match:{value:'Zzqx',matched:false}},
        {terrain:'bsi',text:'12 by 8 to 9 by 6',value:[12,8,9,6],error:'',warnings:[]}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    assert _terrain_posts(r) == [
        ("/author/a7/terrain", {"field": "phase", "value": "3", "handsfree": True}),
        ("/author/a7/terrain", {"field": "location", "value": "Zzqx", "matched": False,
                                "handsfree": True}),
        ("/author/a7/terrain", {"field": "bsi", "value": [12, 8, 9, 6], "handsfree": True})]
    assert r["vals"] == {"phase": "3", "loc": "Zzqx", "a": "12", "d": "6",
                         "reg": "Level 4 · Positive"}
    assert r["cls"]["phase"] == {"tunconf": True}
    assert r["cls"]["loc"] == {"tunconf": True, "unmatched": True}
    assert r["cls"]["a"] == {"tunconf": True}
    assert r["confirm"] == ""
    assert "Terrain: 3. Regenerate" in r["log"] and "Location: Zzqx (not matched)" in r["log"]
    assert "BSI: 12 x 8 to 9 x 6" in r["log"]
    assert r["sess"] == "u"                          # the words join the transcript


@needs_node
def test_js_a_bad_bsi_saves_nothing_and_says_so():
    r = _run("""
      answers['u']=seg([{terrain:'bsi',text:'12 by 8 to 9',value:null,
        error:'heard 3 numbers, not 4',warnings:[]}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    assert _terrain_posts(r) == []
    assert "BSI not understood: 12 by 8 to 9 (heard 3 numbers, not 4). Nothing saved." in r["log"]
    assert r["tstat"].startswith("BSI not understood")
    assert r["sess"] == "u"


@needs_node
def test_js_a_terrain_cue_mid_layer_leaves_the_layer_target_alone():
    r = _run("""
      answers['u']=seg([H('liver','Liver'),{terrain:'phase',text:'2',value:2,name:''},
        H('gut','Gut')]);
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    rows = [(p["url"], p["body"].get("head")) for p in r["calls"]["post"]
            if "/row" in p["url"]]
    assert rows == [("/author/a7/row/7", "Liver"), ("/author/a7/row/7", "Gut")]
    assert r["target"]["gid"] == "g1" and r["target"]["rids"] == ["7"]
    assert r["target"]["layer"] == "4" and r["target"]["n"] == 1


@needs_node
def test_js_a_hand_edit_and_confirm_clear_the_highlight():
    r = _run("""
      answers['u']=seg([{terrain:'phase',text:'3',value:3,name:''},
        {terrain:'location',text:'zzqx',match:{value:'Zzqx',matched:false}}]);
      say(me.ws,'u');await me.chain;
      els.t_phase.value='4';await saveTerrain('phase',els.t_phase);
      calls.mid={phase:Object.keys(els.t_phase._cls),loc:Object.keys(els.t_location._cls)};
      await tConfirm();
    """ + DUMP)
    assert r["calls"]["mid"] == {"phase": [], "loc": ["tunconf", "unmatched"]}
    assert r["cls"]["loc"] == {} and r["confirm"] == "none"


@needs_node
def test_js_also_balances_adds_a_matched_stress_to_the_current_layer():
    r = _run("""
      answers['u']=seg([{also_balances:true,text:'liver',match:{value:'Liver Congestion',matched:true}},
        {also_balances:true,text:'zzqx florp',match:{value:'Zzqx Florp',matched:false}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    adds = [p["body"] for p in r["calls"]["post"] if "/stress/add" in p["url"]]
    assert adds == [{"label": "Liver Congestion", "layer": 4, "rids": [7], "handsfree": True}]
    assert "Layer 1 also balances: Liver Congestion" in r["log"]
    assert "Also balances not matched: zzqx florp. Not added." in r["log"]
    assert r["sess"] == "u"


@needs_node
def test_js_also_balances_with_no_layer_yet_adds_nothing():
    r = _run("""
      answers['u']=seg([{also_balances:true,text:'liver',match:{value:'Liver Congestion',matched:true}}]);
      say(me.ws,'u');await me.chain;
    """ + DUMP, before="cards.length=0;")
    assert [p for p in r["calls"]["post"] if "/stress/add" in p["url"]] == []
    assert "No layer yet for: Liver Congestion. Not added." in r["log"]
