"""Emary asks a spoken question back in the hands-free recording (Glen, 15 November 2026).

Glen asked "Can Emary ask questions if something is said that is unclear?", then chose
"spoken". Only a failure a short answer can fix gets a question: a BSI with the wrong
count or a number out of range, a terrain phase not understood, "also balances" with
no stress matched, and a dose with no remedy. An unmatched name never does: it keeps
its words, highlighted (ruling 4).

The page JS (the fullRecord, recField, handsFree, regulationLevel and terrainSave
blocks) runs under node with a fake DOM, socket, server and speechSynthesis. The fake
server answers the segment route with the real segmenter's output, computed here.
"""
import json
import re
import shutil
import subprocess

import pytest

from dashboard.biofield_layer_match import bsi_heard, segment_utterance
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed on this machine")


# ---- the server: what a failed BSI carries for the question ---------------------------

@pytest.mark.parametrize("words,nums,loc", [
    ("21, 3 and 1", [21, 3, 1], ""),
    ("22 by 3 to 1 by 1", [22, 3, 1, 1], ""),
    ("21 x 3 Liver to 1", [21, 3, 1], "Liver"),
    ("the fourth is 1", [1], ""),
    ("the fourth one is twenty one", [21], ""),
    ("it was 12", [12], ""),
    ("um", [], ""),
])
def test_bsi_heard_reads_every_number(words, nums, loc):
    assert bsi_heard(words) == (nums, loc)


def test_a_failed_bsi_carries_what_was_heard_and_a_good_one_does_not():
    (bad,) = segment_utterance("BSI 21 x 3 Liver to 1")["events"]
    assert bad["value"] is None and bad["heard"] == [21, 3, 1]
    assert bad["heard_location"] == "Liver"
    (good,) = segment_utterance("BSI 21 by 3 to 1 by 1")["events"]
    assert "heard" not in good


# ---- the page JS under node -----------------------------------------------------------

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
 style:{},_cls:{},_attr:{},focus:function(){},scrollIntoView:function(){},
 setAttribute:function(k,v){e._attr[k]=v}};
 e.classList={add:function(c){e._cls[c]=true},remove:function(c){delete e._cls[c]},
  contains:function(c){return !!e._cls[c]}};return e}
var els={};
['g1_head','g1_most','g1_layer','g1_balanced','sessText','astat','hfStat','hfLog','hfBtn',
 'hfAsk','hfAskBtn','rstat','recNote','recBtn','interim',
 't_phase','t_location','bsi_a','bsi_b','bsi_c','bsi_d','bsi_location','t_regulation',
 't_warn','tstat','tConfirm'].forEach(function(id){els[id]=El(id,'')});
els.g1_layer.value='4';els.g1_head.value='Kidney';els.g1_most.value='Bladder';
var cards=[{dataset:{gid:'g1',rids:'7'}}];
var document={activeElement:null,getElementById:function(id){return els[id]||null},
 addEventListener:function(){},querySelector:function(){return null},
 querySelectorAll:function(sel){return sel.indexOf('data-rids')>=0?cards:[]}};
function val(id){var e=document.getElementById(id);return e?e.value:''}
var calls={post:[],saveNow:0,reloads:0};
function scheduleSessionSave(){}
async function saveSessionNow(){calls.saveNow++}
function reloadKeepingView(){calls.reloads++}
function rstat(t){els.rstat.textContent=t}
async function saveLayer(){}
async function addRemedy(){}
var sstore={};
var sessionStorage={getItem:function(k){return k in sstore?sstore[k]:null},
 setItem:function(k,v){sstore[k]=String(v)},removeItem:function(k){delete sstore[k]}};
var lstore={},lsBroken=false;
var localStorage={getItem:function(k){if(lsBroken)throw new Error('blocked');return k in lstore?lstore[k]:null},
 setItem:function(k,v){if(lsBroken)throw new Error('blocked');lstore[k]=String(v)}};
// SEG: the real segmenter's answers, keyed "text|pending", computed in Python.
var SEG=__SEG__;
var stored={phase:null,location:'',bsi:[null,null,null,null],review:{},warnings:[]};
async function post(url,body){calls.post.push({url:url,body:JSON.parse(JSON.stringify(body))});
 if(url.indexOf('/handsfree-segment')>=0){var k=body.text+'|'+(body.pending||'');
  if(!(k in SEG))throw new Error('no segment answer for '+k);
  return JSON.parse(JSON.stringify(SEG[k]))}
 if(/\/terrain$/.test(url)){
  if(body.field==='bsi')stored.bsi=body.value.slice();else stored[body.field]=body.value;
  return Object.assign({ok:true},JSON.parse(JSON.stringify(stored)))}
 if(/\/row$/.test(url))return {ok:true,rid:body.anchor_rid||100};
 return {ok:true}}
async function fetch(u){return {json:async function(){return {key:'k',keyterms:''}}}}
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{
 getUserMedia:async function(){return {getTracks:function(){return [{stop:function(){}}]}}}}}});
function MediaRecorder(s,o){this.state='inactive'}
MediaRecorder.prototype.start=function(){this.state='recording'};
MediaRecorder.prototype.stop=function(){this.state='inactive'};
MediaRecorder.isTypeSupported=function(){return true};window.MediaRecorder=MediaRecorder;
var sockets=[];
function WebSocket(url,proto){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}
WebSocket.prototype.send=function(d){this.sent.push(d)};
WebSocket.prototype.close=function(){this.readyState=3};
function recWs(){return sockets.filter(function(s){return s.url.indexOf('endpointing')<0})[0]}
function say(ws,text,extra){ws.onmessage({data:JSON.stringify(Object.assign({type:'Results',
 is_final:true,speech_final:true,channel:{alternatives:[{transcript:text}]}},extra||{}))})}
function tick(ms){return new Promise(function(r){setTimeout(r,ms||0)})}
// A fake speechSynthesis: what was spoken, and endSpeech() to finish the current one.
var spoken=[],cancels=0,current=null;
function SpeechSynthesisUtterance(t){this.text=t}
var synth={speak:function(u){spoken.push({text:u.text,lang:u.lang,rate:u.rate,
  voice:u.voice&&u.voice.name});current=u},
 cancel:function(){cancels++;var u=current;current=null;if(u&&u.onerror)u.onerror({error:'canceled'})},
 getVoices:function(){return [{name:'Glen clone',lang:'fr-FR',localService:true},
  {name:'Samantha',lang:'en-US',localService:true}]}};
var noSpeech=__NOSPEECH__;
if(!noSpeech){window.speechSynthesis=synth;window.SpeechSynthesisUtterance=SpeechSynthesisUtterance}
function endSpeech(){var u=current;current=null;if(u&&u.onend)u.onend({})}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,log:els.hfLog.textContent,"
        "stat:els.hfStat.textContent,ask:els.hfAsk.textContent,sess:els.sessText.value,"
        "spoken:spoken,cancels:cancels,lstore:lstore,btn:els.hfAskBtn.textContent,"
        "vals:{phase:els.t_phase.value,a:els.bsi_a.value,b:els.bsi_b.value,c:els.bsi_c.value,"
        "d:els.bsi_d.value},hfOpen:_hf!==null}));")


def seg(text, pending=None, match=None):
    """The real segmenter's answer; match is added to each event that the server
    would match (Head, Tail, Remedy, location, also balances)."""
    out = segment_utterance(text, pending=pending)
    for e in out["events"]:
        if e.get("field") in ("head", "tail", "remedy") or e.get("also_balances") \
                or e.get("terrain") == "location":
            e["match"] = match or {"value": e["text"].title(), "matched": False}
    return {"ok": True, **out}


def _run(scenario, segs, before="", no_speech=False):
    table = {f"{t}|{p or ''}": seg(t, p, m) for t, p, m in segs}
    script = (HARNESS.replace("__SEG__", json.dumps(table))
              .replace("__NOSPEECH__", "true" if no_speech else "false")
              + _blocks("fullRecord", "recField", "handsFree", "regulationLevel", "terrainSave")
              + "\nHF_DRAIN_MS=10;HF_FLUSH_MS=0;REC_DRAIN_MS=10;REC_FLUSH_MS=0;"
                "ASK_PAUSE_MS=40;ASK_GUARD_MS=30;\n(async function(){\n" + before +
              "var me=await handsFree();me.ws.onopen();await tick(5);\n" + scenario +
              "\n})().catch(function(e){console.error(e);process.exit(1)});\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# The question is spoken after the pause, then the guard after it ends.
ASKED = "await tick(80);"
ANSWER_READY = "endSpeech();await tick(60);"


def _posts(r, part):
    return [p["body"] for p in r["calls"]["post"] if part in p["url"]]


@needs_node
def test_a_bsi_with_three_numbers_asks_for_the_fourth_and_the_answer_fills_it():
    r = _run("say(me.ws,'BSI 21, 3 and 1.');await me.chain;" + ASKED +
             "calls.asked={spoken:spoken.slice(),ask:els.hfAsk.textContent};" + ANSWER_READY +
             "say(me.ws,'the fourth is 1');await me.chain;" + DUMP,
             [("BSI 21, 3 and 1.", None, None), ("the fourth is 1", "bsi", None),
              ("BSI 21 by 3 to 1 by 1", None, None)])
    q = "I heard 21, 3 and 1. What is the fourth BSI number?"
    assert r["calls"]["asked"]["spoken"] == [
        {"text": q, "lang": "en-US", "rate": 1, "voice": "Samantha"}]
    assert r["calls"]["asked"]["ask"] == "Emary asks: " + q
    assert _posts(r, "/terrain") == [{"field": "bsi", "value": [21, 3, 1, 1], "handsfree": True}]
    assert r["vals"] == {"phase": "", "a": "21", "b": "3", "c": "1", "d": "1"}
    assert "Answered: the fourth is 1" in r["log"] and "Emary asked: " + q in r["log"]
    assert r["ask"] == ""
    # The transcript: Glen's words, the question as one line, then his answer.
    assert r["sess"] == "BSI 21, 3 and 1.\n[Emary asked: " + q + "]\nthe fourth is 1"


@needs_node
def test_a_bsi_with_three_numbers_and_a_location_keeps_the_location():
    r = _run("say(me.ws,'BSI 21 x 3 Liver to 1.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'1');await me.chain;" + DUMP,
             [("BSI 21 x 3 Liver to 1.", None, None), ("1", "bsi", None),
              ("BSI 21 by 3 Liver to 1 by 1", None, {"value": "Liver", "matched": True})])
    assert _posts(r, "/terrain") == [
        {"field": "bsi", "value": [21, 3, 1, 1], "handsfree": True},
        {"field": "bsi_location", "value": "Liver", "matched": True, "handsfree": True}]


@needs_node
def test_a_bsi_number_out_of_range_asks_what_it_was():
    r = _run("say(me.ws,'BSI 22 by 3 to 1 by 1.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'12');await me.chain;" + DUMP,
             [("BSI 22 by 3 to 1 by 1.", None, None), ("12", "bsi", None),
              ("BSI 12 by 3 to 1 by 1", None, None)])
    assert [s["text"] for s in r["spoken"]] == [
        "I heard 22. BSI numbers go from 1 to 21. What was it?"]
    assert _posts(r, "/terrain") == [{"field": "bsi", "value": [12, 3, 1, 1], "handsfree": True}]
    assert "Answered: 12" in r["log"]


@needs_node
def test_a_terrain_phase_not_understood_asks_and_the_answer_fills_it():
    r = _run("say(me.ws,'Terrain looks better today.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'phase 3');await me.chain;" + DUMP,
             [("Terrain looks better today.", None, None), ("phase 3", "terrain", None)])
    assert [s["text"] for s in r["spoken"]] == ["Which terrain phase, 1 to 5?"]
    assert _posts(r, "/terrain") == [{"field": "phase", "value": "3", "handsfree": True}]
    assert "Answered: phase 3" in r["log"]


@needs_node
def test_also_balances_not_matched_asks_and_the_answer_adds_the_stress():
    lc = {"value": "Liver Congestion", "matched": True}
    r = _run("say(me.ws,'Also balances zzqx florp.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'liver congestion');await me.chain;" + DUMP,
             [("Also balances zzqx florp.", None, None),
              ("liver congestion", "also_balances", lc)])
    assert [s["text"] for s in r["spoken"]] == ["Which stress does this layer also balance?"]
    assert _posts(r, "/stress/add") == [{"label": "Liver Congestion", "layer": 4, "rids": [7],
                                         "handsfree": True}]
    assert "Answered: liver congestion" in r["log"]


@needs_node
def test_a_dose_with_no_remedy_asks_which_remedy_and_adds_both():
    dose = json.dumps({"ok": True, "events": [{"field": "dose", "text": "Two drops daily",
                                               "dose": "2 drops", "frequency": "daily"}],
                       "pending": None, "after_remedy": True})
    sob = {"value": "Sobopla", "matched": True}
    r = _run("SEG['Two drops daily.|']=" + dose + ";say(me.ws,'Two drops daily.');await me.chain;"
             + ASKED + ANSWER_READY + "say(me.ws,'Sobopla');await me.chain;" + DUMP,
             [("Sobopla", "remedy", sob)], before="cards.length=0;")
    assert [s["text"] for s in r["spoken"]] == ["Which remedy is that dose for?"]
    rows = [(p["url"], p["body"]) for p in r["calls"]["post"] if "/row" in p["url"]]
    assert rows[0][0] == "/author/a7/row" and rows[0][1]["remedy"] == "Sobopla"
    assert rows[1] == ("/author/a7/row/100", {"handsfree": True, "dosage": "2 drops",
                                              "frequency": "daily"})
    assert "Answered: Sobopla" in r["log"]


@needs_node
def test_unclear_twice_asks_once_more_then_gives_up():
    r = _run("say(me.ws,'Terrain looks better today.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'um maybe');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'not sure');await me.chain;" + ASKED + DUMP,
             [("Terrain looks better today.", None, None), ("um maybe", "terrain", None),
              ("not sure", "terrain", None)])
    assert [s["text"] for s in r["spoken"]] == ["Which terrain phase, 1 to 5?"] * 2
    assert _posts(r, "/terrain") == []
    assert ("No clear answer to: Which terrain phase, 1 to 5? Kept the words in the "
            "transcript.") in r["log"]
    assert r["sess"].endswith("um maybe\n[Emary asked: Which terrain phase, 1 to 5?]\nnot sure")


@needs_node
def test_an_answer_starting_with_a_cue_word_is_a_new_cue():
    r = _run("say(me.ws,'Terrain looks better today.');await me.chain;" + ASKED + ANSWER_READY +
             "say(me.ws,'Head liver');await me.chain;" + ASKED + DUMP,
             [("Terrain looks better today.", None, None),
              ("Head liver", "terrain", {"value": "Liver", "matched": True})])
    assert _posts(r, "/terrain") == []
    assert [(b.get("head"), b.get("handsfree")) for b in _posts(r, "/row/7")] == [("Liver", True)]
    assert "New cue, so the question was dropped: Which terrain phase, 1 to 5?" in r["log"]
    assert len(r["spoken"]) == 1


@needs_node
def test_emary_is_never_transcribed_by_either_stream():
    r = _run("recWs().onopen();"
             "say(me.ws,'Terrain looks better today.');say(recWs(),'Terrain looks better today.');"
             "await me.chain;" + ASKED +
             # While she speaks: both streams hear her.
             "say(me.ws,'Which terrain phase');say(recWs(),'Which terrain phase');"
             "endSpeech();"
             # Within the guard after she ends.
             "say(me.ws,'one to five');say(recWs(),'one to five');"
             "await tick(60);"
             # After the guard, but its audio was during her question: late, still dropped.
             "var t=(Date.now()-_rec.t0)/1000;"
             "say(recWs(),'phase late',{start:t-0.2,duration:0.15});"
             "say(me.ws,'phase late',{start:(Date.now()-me.t0)/1000-0.2,duration:0.15});"
             "await me.chain;"
             "calls.segs=calls.post.filter(function(p){return p.url.indexOf('segment')>=0})"
             ".map(function(p){return p.body.text});"
             "say(me.ws,'phase 3');say(recWs(),'phase 3');await me.chain;" + DUMP,
             [("Terrain looks better today.", None, None), ("phase 3", "terrain", None)],
             before="")
    assert r["calls"]["segs"] == ["Terrain looks better today."]
    assert r["sess"] == ("Terrain looks better today.\n[Emary asked: Which terrain phase, "
                         "1 to 5?]\nphase 3")
    assert _posts(r, "/terrain") == [{"field": "phase", "value": "3", "handsfree": True}]


@needs_node
def test_questions_off_logs_only_and_is_remembered():
    r = _run("askToggle();say(me.ws,'Terrain looks better today.');await me.chain;" + ASKED
             + DUMP, [("Terrain looks better today.", None, None)])
    assert r["spoken"] == [] and r["ask"] == "" and "[Emary" not in r["sess"]
    assert "Terrain phase not understood" in r["log"]
    assert r["lstore"] == {"biofield-hf-questions": "0"} and r["btn"] == "Questions: off"


@needs_node
def test_questions_toggle_works_when_storage_is_blocked():
    r = _run("lsBroken=true;askToggle();say(me.ws,'Terrain looks better today.');"
             "await me.chain;" + ASKED + DUMP, [("Terrain looks better today.", None, None)])
    assert r["spoken"] == [] and r["btn"] == "Questions: off"


@needs_node
def test_stopping_hands_free_cancels_the_question_speaking_and_queued():
    r = _run("say(me.ws,'Terrain looks better today. Also balances zzqx.');await me.chain;"
             + ASKED + "await hfStop();await tick(120);" + DUMP,
             [("Terrain looks better today. Also balances zzqx.", None, None)])
    assert [s["text"] for s in r["spoken"]] == ["Which terrain phase, 1 to 5?"]
    assert r["cancels"] == 1 and r["ask"] == "" and not r["hfOpen"]


@needs_node
def test_questions_wait_for_a_pause_and_never_talk_over_glen():
    r = _run("say(me.ws,'Terrain looks better today.');await me.chain;"
             "for(var i=0;i<6;i++){await tick(15);me.ws.onmessage({data:JSON.stringify({type:"
             "'Results',is_final:false,channel:{alternatives:[{transcript:'still talking'}]}})})}"
             "calls.during=spoken.length;" + ASKED + DUMP,
             [("Terrain looks better today.", None, None)])
    assert r["calls"]["during"] == 0 and len(r["spoken"]) == 1


@needs_node
def test_two_questions_are_asked_one_at_a_time():
    r = _run("say(me.ws,'Terrain looks better today. Also balances zzqx.');await me.chain;"
             + ASKED + "calls.first=spoken.length;" + ANSWER_READY +
             "say(me.ws,'phase 2');await me.chain;" + ASKED + DUMP,
             [("Terrain looks better today. Also balances zzqx.", None, None),
              ("phase 2", "terrain", None)])
    assert r["calls"]["first"] == 1
    assert [s["text"] for s in r["spoken"]] == [
        "Which terrain phase, 1 to 5?", "Which stress does this layer also balance?"]


@needs_node
def test_with_no_speech_the_question_shows_on_screen_and_is_still_answered():
    r = _run("say(me.ws,'Terrain looks better today.');await me.chain;" + ASKED +
             "calls.mid={ask:els.hfAsk.textContent,stat:els.hfStat.textContent};"
             "say(me.ws,'phase 4');await me.chain;" + DUMP,
             [("Terrain looks better today.", None, None), ("phase 4", "terrain", None)],
             no_speech=True)
    assert r["calls"]["mid"]["ask"] == "Emary asks: Which terrain phase, 1 to 5?"
    assert r["calls"]["mid"]["stat"] == "Questions show on screen only: this browser cannot speak."
    assert _posts(r, "/terrain") == [{"field": "phase", "value": "4", "handsfree": True}]


@needs_node
def test_an_unmatched_head_gets_no_question():
    r = _run("say(me.ws,'Head zzqx florp.');await me.chain;" + ASKED + DUMP,
             [("Head zzqx florp.", None, None)])
    assert r["spoken"] == [] and "(not matched)" in r["log"]


def test_the_page_has_the_questions_toggle_and_the_question_line(tmp_path):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    client = create_app(db).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "onclick=handsFree()>Hands-free</button><button id=hfAskBtn type=button" in h
    assert "onclick=askToggle()>Questions: on</button>" in h
    assert "<div id=hfAsk class=food aria-live=polite" in h
    assert "'/chat/tts" not in h[h.index("// Questions back"):h.index("// handsFree:end")]
