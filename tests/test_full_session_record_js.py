"""The full session transcript runs beside every other recording.

Glen, 14 November 2026: "full session transcript should run in any case as a fallback
record of what happened", then "yes" to: it starts by itself with the first recording
of any kind, and keeps running as its own speech-to-text stream beside the short field
recordings and hands-free. Only Stop & save, or leaving the page, stops it.

While it is live, keepTranscript leaves a short or hands-free recording's words to it,
so nothing is written twice. While it is connecting, or after it failed, those words
are kept as before. An unplanned end puts back the words it was left in the last
REC_HOLD_MS.

Runs the page's own JS (the fullRecord, recField and handsFree blocks) under node with
a fake DOM, socket and server.
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
['g0_head','g0_most','g0_layer','g0_balanced','g0_nr_remedy','g0_nr_dosage','g0_nr_frequency',
 'g0_nr_timing','sessText','astat','hfStat','hfLog','hfBtn','rstat','recNote','recBtn',
 'interim'].forEach(function(id){els[id]=El(id,'')});
els.g0_layer.value='1';
var cards=[{dataset:{gid:'g0',rids:'5'}}];
var document={getElementById:function(id){return els[id]||null},addEventListener:function(){},
 querySelector:function(){return null},
 querySelectorAll:function(sel){return sel.indexOf('data-rids')>=0?cards:[]}};
function val(id){var e=document.getElementById(id);return e?e.value:''}
var calls={post:[],sessionSaves:0,saveNow:0,reloads:0,tracksStopped:0};
function scheduleSessionSave(){calls.sessionSaves++}
async function saveSessionNow(){calls.saveNow++}
function reloadKeepingView(){calls.reloads++}
function rstat(t){els.rstat.textContent=t}
async function saveLayer(){}
async function addRemedy(){}
var store={};
var sessionStorage={getItem:function(k){return k in store?store[k]:null},
 setItem:function(k,v){store[k]=String(v)},removeItem:function(k){delete store[k]}};
async function post(url,body){calls.post.push({url:url,body:body});
 if(url.indexOf('/handsfree-segment')>=0)return {ok:true,events:[],pending:null,after_remedy:false};
 if(url.indexOf('/match')>=0)return {ok:true,value:body.text,matched:false};
 return {ok:true}}
// fetchFails: the next n token fetches fail. micDenied: the next n microphone asks fail.
var fetchFails=0,micDenied=0;
async function fetch(u){if(fetchFails>0){fetchFails--;throw new Error('offline')}
 return {json:async function(){return {key:'k',keyterms:''}}}}
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{
 getUserMedia:async function(){if(micDenied>0){micDenied--;var e=new Error('no');e.name='NotAllowedError';throw e}
 return {getTracks:function(){return [{stop:function(){calls.tracksStopped++}}]}}}}}});
function MediaRecorder(s,o){this.state='inactive';this.stream=s}
MediaRecorder.prototype.start=function(){this.state='recording'};
MediaRecorder.prototype.stop=function(){this.state='inactive'};
MediaRecorder.isTypeSupported=function(){return true};
window.MediaRecorder=MediaRecorder;
var sockets=[];
function WebSocket(url,proto){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}
WebSocket.prototype.send=function(d){this.sent.push(d)};
WebSocket.prototype.close=function(){this.readyState=3};
// The full-session socket is the one without endpointing: the short and hands-free
// streams both set it.
function recWs(){return sockets.filter(function(s){return s.url.indexOf('endpointing')<0})[0]}
function say(ws,text){ws.onmessage({data:JSON.stringify({type:'Results',is_final:true,
 speech_final:true,channel:{alternatives:[{transcript:text}]}})})}
function tick(ms){return new Promise(function(r){setTimeout(r,ms||0)})}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,sess:els.sessText.value,"
        "recOn:recOn(),recLive:recLive(),rstat:els.rstat.textContent,"
        "note:els.recNote.textContent,btn:els.recBtn.textContent,"
        "btnOn:els.recBtn.classList.contains('on'),hfOpen:_hf!==null,"
        "recSockets:sockets.filter(function(s){return s.url.indexOf('endpointing')<0}).length,"
        "recOpen:!!recWs()&&recWs().readyState===1,"
        "kinds:sockets.map(function(s){return s.url.indexOf('endpointing=300')>=0?'field':"
        "s.url.indexOf('endpointing=500')>=0?'handsfree':'record'}),store:store}));")


def _run(scenario):
    script = (HARNESS + _block("fullRecord") + _block("recField") + _block("handsFree")
              + "\nHF_DRAIN_MS=40;HF_FLUSH_MS=0;REC_DRAIN_MS=40;REC_FLUSH_MS=0;FIELD_CAP_MS=300;\n"
              "(async function(){\n" + scenario + "\n})()"
              ".catch(function(e){console.error(e);process.exit(1)});\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


FIELD = "var f=await recField('g0_head','head');await tick(5);\n"
HANDS = "var h=await handsFree();await tick(5);\n"


def test_a_short_field_recording_starts_the_full_transcript():
    r = _run(FIELD + "f.ws.onopen();recWs().onopen();" + DUMP)
    assert sorted(r["kinds"]) == ["field", "record"]
    assert r["recOn"] and r["recLive"] and r["recOpen"]
    assert r["btnOn"] and "Recording" in r["btn"]


def test_hands_free_starts_the_full_transcript_and_is_not_refused():
    r = _run(HANDS + "h.ws.onopen();" + DUMP)
    assert sorted(r["kinds"]) == ["handsfree", "record"]
    assert r["hfOpen"] and r["recOn"]


def test_hands_free_runs_beside_a_record_already_running():
    r = _run("await recStart();recWs().onopen();\n" + HANDS + "h.ws.onopen();" + DUMP)
    assert r["recSockets"] == 1 and r["hfOpen"] and r["recLive"]


def test_while_the_full_transcript_is_live_a_field_recording_does_not_add_its_words_twice():
    r = _run(FIELD + """
      f.ws.onopen();recWs().onopen();
      say(recWs(),'liver congestion');say(f.ws,'liver congestion');await f.heard;
    """ + DUMP)
    assert r["sess"] == "liver congestion"


def test_hands_free_words_are_not_added_twice_either():
    r = _run(HANDS + """
      h.ws.onopen();recWs().onopen();
      say(recWs(),'Head liver.');say(h.ws,'Head liver.');await h.chain;
    """ + DUMP)
    assert r["sess"] == "Head liver."


def test_words_heard_while_the_full_transcript_is_still_connecting_are_kept():
    # Its socket has not opened, so its recorder has not started and it heard nothing.
    r = _run(FIELD + "f.ws.onopen();say(f.ws,'liver');await f.heard;" + DUMP)
    assert r["sess"] == "liver" and r["recOn"] and not r["recLive"]


def test_if_it_fails_to_start_the_field_recording_goes_on_and_says_so():
    # The full transcript asks for its token first, so the one failure is its own.
    r = _run("fetchFails=1;" + FIELD + "f.ws.onopen();say(f.ws,'liver');await f.heard;" + DUMP)
    assert r["kinds"] == ["field"]
    assert r["sess"] == "liver"
    assert "The full transcript is not running" in r["note"]
    assert not r["recOn"] and not r["btnOn"]


def test_if_the_microphone_is_refused_to_it_hands_free_goes_on_and_says_so():
    r = _run("micDenied=1;" + HANDS + "h.ws.onopen();say(h.ws,'Head liver.');await h.chain;"
             + DUMP)
    assert r["hfOpen"] and r["sess"] == "Head liver."
    assert "The full transcript is not running" in r["note"] and "microphone" in r["note"]


def test_an_unplanned_end_puts_back_the_words_it_was_left_and_keeps_later_ones():
    r = _run(FIELD + """
      f.ws.onopen();recWs().onopen();
      say(f.ws,'liver');await f.heard;
      recWs().readyState=3;recWs().onclose({code:1011});
      calls.afterClose={note:els.recNote.textContent,on:recOn(),btnOn:els.recBtn.classList.contains('on'),
        sess:els.sessText.value};
      // The next short recording starts it again, and keeps its words while it connects.
      var g=await recField('g0_most','tail');await tick(5);g.ws.onopen();say(g.ws,'kidney');await g.heard;
    """ + DUMP)
    a = r["calls"]["afterClose"]
    assert a["sess"] == "liver"
    assert "The full transcript stopped" in a["note"] and not a["on"] and not a["btnOn"]
    assert r["sess"] == "liver kidney"
    assert r["recSockets"] == 2 and r["recOn"]


def test_stopping_hands_free_or_a_field_recording_leaves_it_running():
    r = _run(HANDS + """
      h.ws.onopen();recWs().onopen();
      await handsFree();
      var f=await recField('g0_head','head');await tick(5);f.ws.onopen();stopField();
      h.ws.onerror();
    """ + DUMP)
    assert r["recSockets"] == 1 and r["recOpen"] and r["recLive"] and not r["hfOpen"]


def test_a_failing_field_stream_does_not_stop_it():
    r = _run(FIELD + "f.ws.onopen();recWs().onopen();f.ws.onerror();" + DUMP)
    assert r["recLive"] and r["recOpen"]


def test_its_own_stop_drains_closes_and_saves():
    r = _run(FIELD + """
      f.ws.onopen();recWs().onopen();var w=recWs();
      var p=recStop();say(w,'last words');await p;
    """ + DUMP)
    assert r["sess"] == "last words"
    assert not r["recOn"] and not r["recOpen"] and not r["btnOn"]
    assert r["calls"]["saveNow"] == 1


def test_a_reload_with_it_on_starts_it_again_but_a_later_visit_does_not():
    r = _run(FIELD + """
      recWs().onopen();recLeave();_rec=null;sockets.length=0;
      await recResume();await tick(5);
    """ + DUMP)
    assert r["recSockets"] == 1 and r["store"] == {}
    r = _run("""
      store['biofield-rec-a7']=String(Date.now()-60000);await recResume();await tick(5);
    """ + DUMP)
    assert r["recSockets"] == 0 and not r["recOn"]


def test_the_record_button_can_show_it_is_running_and_the_refusal_is_gone():
    from dashboard.biofield_report_html import _AUTHOR_JS
    import dashboard.biofield_report_html as m
    src = open(m.__file__, encoding="utf-8").read()
    assert "<button id=recBtn class=btn onclick=recStart()>" in src
    assert "#recBtn.on" in src and "<div id=recNote class=food></div>" in src
    assert "Stop the Record first" not in _AUTHOR_JS
