"""The editor's short recording per field (live session part 2a, ruling 1).

recField opens a short Deepgram stream and joins its final segments until the
utterance ends (speech_final, UtteranceEnd, the socket closing, or a 15 second cap).
It keeps the words in the full transcript, asks /match, and fills that one field. An unmatched
phrase keeps its words with the unmatched class (ruling 4). A matched Head or Tail
saves its own field as matched, an unmatched one as unmatched, never another
field's (ruling 7, review of 14 November). "balanced" ticks the
card (ruling 2) and focuses the next layer's Head.

Runs the page's own JS under node with a fake DOM, socket and server.
"""
import json
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    not NODE, reason="node is not installed on this machine, so the page JS cannot be run")


def _page_js():
    from dashboard.biofield_report_html import _AUTHOR_JS
    m = re.search(r"// recField:begin\n(.*?)// recField:end", _AUTHOR_JS, re.S)
    assert m, "recField markers missing from _AUTHOR_JS"
    return m.group(1).replace("__TID__", "a7")


HARNESS = r"""
var window=globalThis;
function El(id,v){var e={id:id,value:v||'',textContent:'',title:'',checked:false,dataset:{},
 _cls:{},focused:false,focus:function(){this.focused=true;document.activeElement=this},
 scrollIntoView:function(){}};
 e.classList={add:function(c){e._cls[c]=true},remove:function(c){delete e._cls[c]},
  contains:function(c){return !!e._cls[c]}};return e}
var els={};
['g0_head','g0_most','g0_nr_remedy','g0_nr_dosage','g0_nr_frequency','g0_nr_timing','g0_balanced',
 'gnew_head','gnew_most','gnew_nr_remedy','gnew_nr_dosage','gnew_nr_frequency','gnew_nr_timing',
 'sessText','astat'].forEach(function(id){els[id]=El(id,'')});
var cards={g0:{dataset:{gid:'g0',rids:'5,6'},_cls:{}},gnew:{dataset:{gid:'gnew'},_cls:{}}};
Object.keys(cards).forEach(function(k){var c=cards[k];
 c.classList={add:function(x){c._cls[x]=true},remove:function(x){delete c._cls[x]}}});
var mics={};
var listeners={};
var document={activeElement:null,getElementById:function(id){return els[id]||null},
 addEventListener:function(type,fn){listeners[type]=fn},
 querySelector:function(sel){var m=/data-mic="([^"]+)"/.exec(sel);
  if(m){if(!mics[m[1]]){mics[m[1]]=El('mic-'+m[1])}return mics[m[1]]}
  m=/data-gid="([^"]+)"/.exec(sel);if(m)return cards[m[1]]||null;return null}};
function type(id,v){els[id].value=v;listeners.input({target:els[id]})}
var calls={post:[],saveLayer:[],addRemedy:[],sessionSaves:0,tracksStopped:0,wsClosed:0};
var _sess='';
function scheduleSessionSave(){calls.sessionSaves++}
// The full-session Record block is not loaded here: tests/test_full_session_record_js.py
// runs it with this one. Here it is off, so keepTranscript keeps every word.
calls.recAuto=0;function recAuto(){calls.recAuto++;return null}
function recCovers(){return false}
async function saveLayer(gid,btn,voice){calls.saveLayer.push({gid:gid,voice:voice,
 head:els[gid+'_head'].value,most:els[gid+'_most'].value})}
async function addRemedy(gid){calls.addRemedy.push({gid:gid,remedy:els[gid+'_nr_remedy'].value,
 dosage:els[gid+'_nr_dosage'].value,frequency:els[gid+'_nr_frequency'].value,
 timing:els[gid+'_nr_timing'].value,voice:voiceFlags(gid)})}
var answers={},gates={};
async function post(url,body){calls.post.push({url:url,body:body});
 if(url.indexOf('/match')>=0){if(gates[body.text])await gates[body.text].p;
  return answers[body.text]||{ok:true,value:body.text,matched:false}}
 if(url.indexOf('/layer-balanced')>=0)return {ok:true,updated:2,balanced:body.balanced};
 return {ok:true}}
function gate(text){var g={};g.p=new Promise(function(r){g.open=r});gates[text]=g;return g}
async function fetch(u){return {json:async function(){return {key:'k',keyterms:''}}}}
// node 21+ has a read-only global navigator, so the fake is defined over it.
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
function msg(ws,text,final,speech){ws.onmessage({data:JSON.stringify({type:'Results',
 is_final:!!final,speech_final:!!speech,channel:{alternatives:[{transcript:text}]}})})}
function uend(ws){ws.onmessage({data:JSON.stringify({type:'UtteranceEnd'})})}
function tick(ms){return new Promise(function(r){setTimeout(r,ms||0)})}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,sess:els.sessText.value,"
        "vals:Object.keys(els).reduce(function(o,k){o[k]=els[k].value;return o},{}),"
        "unmatched:Object.keys(els).filter(function(k){return els[k].classList.contains('unmatched')}),"
        "voice:Object.keys(els).reduce(function(o,k){if(els[k].dataset.voice!==undefined)"
        "o[k]=els[k].dataset.voice;return o},{}),"
        "checked:els.g0_balanced.checked,focused:document.activeElement&&document.activeElement.id,"
        "cardBalanced:!!cards.g0._cls.lbalanced,stat:els.astat.textContent,"
        "rfOpen:_rf!==null,sockets:sockets.length,url:sockets.length?sockets[0].url:'',"
        "micOn:Object.keys(mics).filter(function(k){return mics[k].classList.contains('on')})}));")


def _run(scenario):
    script = HARNESS + _page_js() + "\n(async function(){\n" + scenario + "\n})()" \
        ".catch(function(e){console.error(e);process.exit(1)});\n"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _speak(input_id, kind, *messages, var="me"):
    """recField on one input, then socket messages (text, is_final, speech_final),
    then wait for the fill."""
    lines = [f"var {var}=await recField('{input_id}','{kind}');{var}.ws.onopen();"]
    for text, final, speech in messages:
        lines.append(f"msg({var}.ws,{json.dumps(text)},{str(final).lower()},{str(speech).lower()});")
    lines.append(f"await {var}.heard;")
    return "\n".join(lines)


def test_final_segments_join_until_the_utterance_ends():
    """Review 2026-11-14: is_final ends a segment, not the utterance."""
    r = _run("""
      answers['rose essence two drops twice a day']={ok:true,value:'Perelandra Rose Essence',
        matched:true,dose:'2 drops',frequency:'twice a day',
        dosing:{dosage:'2 drops',frequency:'twice a day',timing:'under the tongue'}};
    """ + _speak("g0_nr_remedy", "remedy", ("rose", False, False),
                 ("rose essence", True, False), ("two drops", True, False),
                 ("twice a day", True, True), ("after the end", True, True)) + DUMP)
    assert [p["body"] for p in r["calls"]["post"]] == [
        {"kind": "remedy", "text": "rose essence two drops twice a day"}]
    assert r["sess"] == "rose essence two drops twice a day"
    assert r["calls"]["wsClosed"] == 1 and r["calls"]["tracksStopped"] == 1
    assert r["rfOpen"] is False and r["micOn"] == []
    # The full session transcript starts beside it (Glen, 14 November 2026).
    assert r["calls"]["recAuto"] == 1
    assert "endpointing=" in r["url"] and "utterance_end_ms=" in r["url"]


def test_utterance_end_message_also_ends_it():
    r = _run("""
      var me=await recField('g0_head','head');me.ws.onopen();
      msg(me.ws,'liver',true,false);msg(me.ws,'congestion',true,false);uend(me.ws);await me.heard;
    """ + DUMP)
    assert [p["body"]["text"] for p in r["calls"]["post"]] == ["liver congestion"]


def test_the_hard_cap_ends_a_recording_that_never_hears_an_end():
    r = _run("""
      FIELD_CAP_MS=30;
      var me=await recField('g0_head','head');me.ws.onopen();
      msg(me.ws,'liver',true,false);await tick(80);await me.heard;
    """ + DUMP)
    assert [p["body"]["text"] for p in r["calls"]["post"]] == ["liver"]
    assert r["rfOpen"] is False


def test_one_utterance_fills_one_field_only():
    r = _run("""
      answers['liver congestion']={ok:true,value:'Liver Congestion',matched:true,also_tail:false};
    """ + _speak("g0_head", "head", ("liver congestion", True, True)) + DUMP)
    v = r["vals"]
    assert v["g0_head"] == "Liver Congestion"
    assert v["g0_most"] == "" and v["gnew_head"] == "" and v["g0_nr_remedy"] == ""
    assert r["calls"]["sessionSaves"] == 1
    # The save carries this field's match alone, never a confirmation for the card.
    assert r["calls"]["saveLayer"] == [{"gid": "g0", "voice": {"head": True},
                                        "head": "Liver Congestion", "most": ""}]
    assert r["unmatched"] == [] and r["voice"] == {"g0_head": "1"}


def test_unmatched_words_stay_with_the_unmatched_class_and_save_unmatched():
    r = _run("""
      els.sessText.value='earlier words';
      answers['zzqx florp']={ok:true,value:'Zzqx Florp',matched:false,also_tail:false};
    """ + _speak("g0_most", "tail", ("zzqx florp", True, True)) + DUMP)
    assert r["vals"]["g0_most"] == "Zzqx Florp"
    assert r["unmatched"] == ["g0_most"]
    assert r["calls"]["saveLayer"][0]["voice"] == {"most_affected": False}
    assert r["sess"] == "earlier words zzqx florp"


def test_typing_clears_the_mark():
    r = _run("""
      answers['zzqx']={ok:true,value:'Zzqx',matched:false};
    """ + _speak("gnew_head", "head", ("zzqx", True, True)) + """
      type('gnew_head','Zzqx corrected');
    """ + DUMP)
    assert r["unmatched"] == [] and r["voice"] == {}


def test_head_and_tail_fills_both_fields():
    r = _run("""
      answers['head and tail liver']={ok:true,value:'Liver',matched:true,also_tail:true};
    """ + _speak("g0_head", "head", ("head and tail liver", True, True)) + DUMP)
    assert (r["vals"]["g0_head"], r["vals"]["g0_most"]) == ("Liver", "Liver")
    assert r["calls"]["saveLayer"][0]["voice"] == {"head": True, "most_affected": True}


def test_new_layer_carries_each_fields_match_to_add():
    r = _run("""
      answers['kidney']={ok:true,value:'Kidney',matched:true,also_tail:false};
      answers['florp 10 drops twice a day']={ok:true,value:'Florp',matched:false,
        dose:'10 drops',frequency:'twice a day',
        dosing:{dosage:'10 drops',frequency:'twice a day',timing:''}};
    """ + _speak("gnew_head", "head", ("kidney", True, True)) + "\n"
        + _speak("gnew_nr_remedy", "remedy", ("florp 10 drops twice a day", True, True), var="me2")
        + DUMP)
    assert r["calls"]["saveLayer"] == []            # no rows yet on the new card
    assert r["calls"]["addRemedy"] == [{
        "gid": "gnew", "remedy": "Florp", "dosage": "10 drops", "frequency": "twice a day",
        "timing": "", "voice": {"head": True, "remedy": False}}]
    assert r["unmatched"] == ["gnew_nr_remedy"]


def test_a_new_remedy_resets_the_previous_products_dosing():
    """Review 2026-11-14: remedy B must not keep product A's defaults."""
    r = _run("""
      els.g0_nr_dosage.value='3 drops';els.g0_nr_frequency.value='3 times a day';
      els.g0_nr_timing.value='under the tongue';
      answers['sulfur syntropy']={ok:true,value:'Sulfur Syntropy',matched:true,dose:'',frequency:'',
        dosing:{dosage:'1 capsule',frequency:'twice a day',timing:''}};
    """ + _speak("g0_nr_remedy", "remedy", ("sulfur syntropy", True, True)) + DUMP)
    assert r["calls"]["addRemedy"][0] == {
        "gid": "g0", "remedy": "Sulfur Syntropy", "dosage": "1 capsule",
        "frequency": "twice a day", "timing": "", "voice": {"remedy": True}}


def test_saying_balanced_ticks_the_card_and_focuses_the_next_head():
    r = _run("""
      answers['balanced']={ok:true,value:'',matched:true,balanced:true};
    """ + _speak("g0_head", "head", ("balanced", True, True)) + DUMP)
    assert r["checked"] is True and r["cardBalanced"] is True
    assert r["focused"] == "gnew_head"
    lb = [p for p in r["calls"]["post"] if "layer-balanced" in p["url"]]
    assert lb == [{"url": "/author/a7/layer-balanced", "body": {"rids": ["5", "6"],
                                                                 "balanced": True}}]
    assert r["vals"]["g0_head"] == "" and r["calls"]["saveLayer"] == []


def test_tapping_the_same_mic_again_stops_and_later_messages_write_nothing():
    r = _run("""
      var me=await recField('g0_head','head');me.ws.onopen();
      msg(me.ws,'liver',true,false);
      await recField('g0_head','head');
      msg(me.ws,'congestion',true,true);uend(me.ws);me.ws.onclose();await tick(5);
    """ + DUMP)
    assert r["rfOpen"] is False and r["stat"] == "Stopped."
    assert r["calls"]["post"] == [] and r["vals"]["g0_head"] == ""


def test_a_superseded_recording_writes_nothing():
    r = _run("""
      var a=await recField('g0_head','head');a.ws.onopen();
      var b=await recField('g0_most','tail');b.ws.onopen();
      msg(a.ws,'liver',true,true);await tick(5);
      stopField();                    // b's 15 second cap would keep node waiting
    """ + DUMP)
    assert r["calls"]["post"] == [] and r["vals"]["g0_head"] == ""


def test_a_late_match_does_not_overwrite_a_hand_correction():
    r = _run("""
      var g=gate('liver');
      answers['liver']={ok:true,value:'Liver',matched:true,also_tail:false};
      var me=await recField('g0_head','head');me.ws.onopen();msg(me.ws,'liver',true,true);
      type('g0_head','Kidney');       // Glen corrects it while /match is still out
      g.open();await me.heard;
    """ + DUMP)
    assert r["vals"]["g0_head"] == "Kidney"
    assert r["calls"]["saveLayer"] == [] and r["voice"] == {}


def test_a_late_match_does_not_overwrite_a_newer_recording_of_the_field():
    r = _run("""
      var g=gate('liver');
      answers['liver']={ok:true,value:'Liver',matched:true,also_tail:false};
      answers['kidney']={ok:true,value:'Kidney',matched:true,also_tail:false};
      var a=await recField('g0_head','head');a.ws.onopen();msg(a.ws,'liver',true,true);
      var b=await recField('g0_head','head');b.ws.onopen();msg(b.ws,'kidney',true,true);
      await b.heard;g.open();await a.heard;
    """ + DUMP)
    assert r["vals"]["g0_head"] == "Kidney"
    assert [c["head"] for c in r["calls"]["saveLayer"]] == ["Kidney"]


def test_head_and_tail_skips_a_tail_typed_after_the_recording_began():
    r = _run("""
      var g=gate('head and tail liver');
      answers['head and tail liver']={ok:true,value:'Liver',matched:true,also_tail:true};
      var me=await recField('g0_head','head');me.ws.onopen();msg(me.ws,'head and tail liver',true,true);
      type('g0_most','Gut');g.open();await me.heard;
    """ + DUMP)
    assert (r["vals"]["g0_head"], r["vals"]["g0_most"]) == ("Liver", "Gut")
    assert r["calls"]["saveLayer"][0]["voice"] == {"head": True}
