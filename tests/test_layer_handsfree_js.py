"""The editor's hands-free toggle (live session part 2b).

One Deepgram stream runs until the toggle is turned off. Each finished utterance joins
the full transcript, goes to /handsfree-segment, and its events are applied in order
through the row routes with handsfree set (ruling 7: every row unconfirmed). Writes run
one at a time through one promise chain. Once the toggle is off, nothing more is
applied, even an answer already on its way.

Runs the page's own JS (the recField and handsFree blocks) under node with a fake DOM,
socket and server.
"""
import json
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    not NODE, reason="node is not installed on this machine, so the page JS cannot be run")


def _block(name):
    from dashboard.biofield_report_html import _AUTHOR_JS
    m = re.search(r"// %s:begin\n(.*?)// %s:end" % (name, name), _AUTHOR_JS, re.S)
    assert m, f"{name} markers missing from _AUTHOR_JS"
    return m.group(1).replace("__TID__", "a7")


HARNESS = r"""
var window=globalThis;
function El(id,v){var e={id:id,value:v||'',textContent:'',title:'',checked:false,dataset:{},
 _cls:{},focus:function(){},scrollIntoView:function(){}};
 e.classList={add:function(c){e._cls[c]=true},remove:function(c){delete e._cls[c]},
  contains:function(c){return !!e._cls[c]}};return e}
var els={};
['g0_head','g0_most','g0_layer','g0_balanced','g1_head','g1_most','g1_layer','g1_balanced',
 'sessText','astat','hfStat','hfLog','hfBtn'].forEach(function(id){els[id]=El(id,'')});
els.g0_layer.value='1';els.g1_layer.value='4';els.g1_head.value='Kidney';els.g1_most.value='Bladder';
var cards=[{dataset:{gid:'g0',rids:'5'}},{dataset:{gid:'g1',rids:'7,8'}}];
var document={getElementById:function(id){return els[id]||null},addEventListener:function(){},
 querySelector:function(){return null},
 querySelectorAll:function(sel){return sel.indexOf('data-rids')>=0?cards:[]}};
function val(id){var e=document.getElementById(id);return e?e.value:''}
var calls={post:[],sessionSaves:0,saveNow:0,reloads:0,tracksStopped:0,wsClosed:0};
var _sess='',_dg=null;
function scheduleSessionSave(){calls.sessionSaves++}
async function saveSessionNow(){calls.saveNow++}
function reloadKeepingView(){calls.reloads++}
var answers={},gates={},nextRid=100;
async function post(url,body){calls.post.push({url:url,body:JSON.parse(JSON.stringify(body))});
 if(url.indexOf('/handsfree-segment')>=0){if(gates[body.text])await gates[body.text].p;
  return answers[body.text]||{ok:true,events:[],pending:null,after_remedy:false}}
 if(url.indexOf('/layer-balanced')>=0)return {ok:true,updated:body.rids.length};
 if(/\/row$/.test(url))return {ok:true,rid:body.anchor_rid||nextRid++};
 return {ok:true}}
function gate(text){var g={};g.p=new Promise(function(r){g.open=r});gates[text]=g;return g}
async function fetch(u){return {json:async function(){return {key:'k',keyterms:''}}}}
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{
 getUserMedia:async function(){
 return {getTracks:function(){return [{stop:function(){calls.tracksStopped++}}]}}}}}});
function MediaRecorder(s,o){this.state='inactive';this.stream=s}
MediaRecorder.prototype.start=function(){this.state='recording'};
MediaRecorder.prototype.stop=function(){this.state='inactive'};
MediaRecorder.isTypeSupported=function(){return true};
window.MediaRecorder=MediaRecorder;
var sockets=[];
function WebSocket(url,proto){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}
WebSocket.prototype.send=function(d){this.sent.push(d)};
WebSocket.prototype.close=function(){this.readyState=3;calls.wsClosed++};
function say(ws,text){ws.onmessage({data:JSON.stringify({type:'Results',is_final:true,
 speech_final:true,channel:{alternatives:[{transcript:text}]}})})}
function part(ws,text){ws.onmessage({data:JSON.stringify({type:'Results',is_final:true,
 speech_final:false,channel:{alternatives:[{transcript:text}]}})})}
function uend(ws){ws.onmessage({data:JSON.stringify({type:'UtteranceEnd'})})}
function tick(ms){return new Promise(function(r){setTimeout(r,ms||0)})}
function H(t,v,ok){return {field:'head',text:t,both:false,match:{value:v,matched:ok}}}
function T(t,v,ok){return {field:'tail',text:t,both:false,match:{value:v,matched:ok}}}
function R(t,v,ok){return {field:'remedy',text:t,both:false,match:{value:v,matched:ok,
 dosing:{dosage:'1 capsule',frequency:'daily',timing:''}}}}
function seg(evs,pending,after){return {ok:true,events:evs,pending:pending||null,after_remedy:!!after}}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,sess:els.sessText.value,"
        "stat:els.hfStat.textContent,log:els.hfLog.textContent,hfOpen:_hf!==null,"
        "btn:els.hfBtn.textContent,url:sockets.length?sockets[0].url:'',"
        "g1head:els.g1_head.value}));")


def _run(scenario):
    script = (HARNESS + _block("recField") + _block("handsFree")
              + "\n(async function(){\n" + scenario + "\n})()"
              ".catch(function(e){console.error(e);process.exit(1)});\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


START = "var me=await handsFree();me.ws.onopen();\n"


def _writes(r):
    return [(p["url"], p["body"]) for p in r["calls"]["post"]
            if "/handsfree-segment" not in p["url"]]


def test_an_utterance_lands_in_the_transcript_and_writes_unconfirmed_rows():
    r = _run("""
      els.sessText.value='earlier';
      answers['Head liver. Remedy sobopla.']=seg([H('liver','Liver',true),R('sobopla','Sobopla',true)],null,true);
    """ + START + """
      say(me.ws,'Head liver. Remedy sobopla.');await me.chain;
    """ + DUMP)
    assert r["sess"] == "earlier Head liver. Remedy sobopla."
    seg = [p["body"] for p in r["calls"]["post"] if "/handsfree-segment" in p["url"]]
    assert seg == [{"text": "Head liver. Remedy sobopla.", "pending": None, "after_remedy": False}]
    # Starts on the last card (g1, stored layer 4, rows 7 and 8), as it is not balanced.
    assert _writes(r) == [
        ("/author/a7/row/7", {"head": "Liver", "most_affected": "Bladder",
                              "voice": {"head": True}, "handsfree": True}),
        ("/author/a7/row/8", {"head": "Liver", "most_affected": "Bladder",
                              "voice": {"head": True}, "handsfree": True}),
        ("/author/a7/row", {"layer": "4", "anchor_rid": "7", "head": "Liver",
                            "most_affected": "Bladder", "remedy": "Sobopla",
                            "dosage": "1 capsule", "frequency": "daily", "timing": "",
                            "voice": {"remedy": True}, "handsfree": True})]
    assert r["g1head"] == "Liver"
    assert "wss://" in r["url"] and "utterance_end_ms=" in r["url"]
    assert "Layer 2: say head, tail or remedy." in r["stat"]


def test_segments_join_until_the_utterance_ends():
    r = _run(START + """
      part(me.ws,'Head');part(me.ws,'liver congestion');uend(me.ws);await me.chain;
    """ + DUMP)
    seg = [p["body"]["text"] for p in r["calls"]["post"] if "/handsfree-segment" in p["url"]]
    assert seg == ["Head liver congestion"]


def test_writes_are_ordered_even_when_an_answer_is_slow():
    r = _run("""
      var g=gate('first');
      answers['first']=seg([H('liver','Liver',true)]);
      answers['second']=seg([T('gut','Gut',false)]);
    """ + START + """
      say(me.ws,'first');say(me.ws,'second');await tick(5);
      var before=calls.post.length;          // only the first segment call is out
      g.open();await me.chain;
      calls.before=before;
    """ + DUMP)
    assert r["calls"]["before"] == 1
    urls = [p["url"] + ":" + (p["body"].get("text") or p["body"].get("head") or "")
            for p in r["calls"]["post"]]
    assert urls == ["/author/a7/handsfree-segment:first", "/author/a7/row/7:Liver",
                    "/author/a7/row/8:Liver", "/author/a7/handsfree-segment:second",
                    "/author/a7/row/7:Liver", "/author/a7/row/8:Liver"]
    tail = [b for u, b in _writes(r)][-1]
    assert tail["most_affected"] == "Gut" and tail["voice"] == {"most_affected": False}


def test_turned_off_nothing_more_is_applied():
    r = _run("""
      var g=gate('late');
      answers['late']=seg([H('liver','Liver',true),{balanced:true}]);
    """ + START + """
      say(me.ws,'late');await tick(5);
      var off=handsFree();                    // toggle off while /handsfree-segment is out
      g.open();await off;
      say(me.ws,'after off');uend(me.ws);me.ws.onclose();await me.chain;
    """ + DUMP)
    assert _writes(r) == []
    seg = [p["body"]["text"] for p in r["calls"]["post"] if "/handsfree-segment" in p["url"]]
    assert seg == ["late"]
    assert r["sess"] == "late"                       # the words were kept all the same
    assert r["hfOpen"] is False and r["btn"] == "Hands-free"
    assert r["calls"]["wsClosed"] == 1 and r["calls"]["tracksStopped"] == 1
    assert r["calls"]["saveNow"] == 1 and r["calls"]["reloads"] == 0


def test_an_event_queued_behind_the_off_switch_is_dropped_mid_utterance():
    """Three events, off after the first write: the second and third never go out."""
    r = _run("""
      answers['three']=seg([H('liver','Liver',true),T('gut','Gut',true),{balanced:true}]);
      var realPost=post;var n=0;
      var off=null;
      post=async function(u,b){var j=await realPost(u,b);if(/row\\/7$/.test(u)&&!n++)off=hfStop();return j};
    """ + START + """
      say(me.ws,'three');await me.chain;await off;
    """ + DUMP)
    assert [u for u, _ in _writes(r)] == ["/author/a7/row/7", "/author/a7/row/8"]
    assert r["calls"]["reloads"] == 1          # something was written, so the page reloads


def test_next_layer_starts_a_new_layer_and_balanced_ticks_the_current_one():
    r = _run("""
      answers['u']=seg([{balanced:true},{next_layer:true},H('kidney','Kidney',true),
        R('tmg','TMG',false),{balanced:true}]);
    """ + START + """
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    assert _writes(r) == [
        ("/author/a7/layer-balanced", {"rids": ["7", "8"], "balanced": True}),
        # The new layer goes after the highest stored layer (4), and its Head starts a row.
        ("/author/a7/row", {"layer": "5", "head": "Kidney", "most_affected": "",
                            "remedy": "", "voice": {"head": True}, "handsfree": True}),
        ("/author/a7/row", {"layer": "5", "anchor_rid": "100", "head": "Kidney",
                            "most_affected": "", "remedy": "TMG", "dosage": "1 capsule",
                            "frequency": "daily", "timing": "", "voice": {"remedy": False},
                            "handsfree": True}),
        ("/author/a7/layer-balanced", {"rids": ["100"], "balanced": True})]
    assert "Layer 3 remedy: TMG (not matched)" in r["log"]
    assert "Layer 3 started." in r["log"]


def test_a_balanced_last_card_starts_on_a_new_layer():
    r = _run("""
      els.g1_balanced.checked=true;
      answers['u']=seg([{balanced:true},T('gut','Gut',true)]);
    """ + START + """
      say(me.ws,'u');await me.chain;
    """ + DUMP)
    assert _writes(r) == [("/author/a7/row", {
        "layer": "5", "head": "", "most_affected": "Gut", "remedy": "",
        "voice": {"most_affected": True}, "handsfree": True})]
    assert "Nothing to mark balanced yet on layer 3." in r["log"]


def test_an_open_field_and_a_dose_carry_to_the_next_utterance():
    r = _run("""
      answers['Remedy sobopla.']=seg([R('sobopla','Sobopla',true)],null,true);
      answers['Two drops twice a day. Head.']=seg([{field:'dose',text:'Two drops twice a day',
        dose:'2 drops',frequency:'twice a day'}],'head',false);
    """ + START + """
      say(me.ws,'Remedy sobopla.');say(me.ws,'Two drops twice a day. Head.');await me.chain;
    """ + DUMP)
    seg = [p["body"] for p in r["calls"]["post"] if "/handsfree-segment" in p["url"]]
    assert seg[1] == {"text": "Two drops twice a day. Head.", "pending": None,
                      "after_remedy": True}
    assert _writes(r)[-1] == ("/author/a7/row/7", {"handsfree": True, "dosage": "2 drops",
                                                   "frequency": "twice a day"})
    assert "the next words go to Head." in r["stat"]


def test_it_will_not_run_beside_the_whole_session_record():
    r = _run("""
      _dg={readyState:1};var me=await handsFree();
    """ + DUMP)
    assert me_none(r) and "Stop the Record first" in r["stat"]


def me_none(r):
    return r["hfOpen"] is False and r["url"] == ""
