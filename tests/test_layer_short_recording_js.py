"""The editor's short recording per field (live session part 2a, ruling 1).

recField opens a short Deepgram stream, stops at the first final result, keeps the
words in the full transcript, asks /match, and fills that one field. An unmatched
phrase keeps its words with the unmatched class (ruling 4). A matched Head or Tail
saves confirmed=1, an unmatched one confirmed=0 (ruling 7). "balanced" ticks the
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
var calls={post:[],saveLayer:[],addRemedy:[],sessionSaves:0,tracksStopped:0,wsClosed:0};
var _sess='';
function scheduleSessionSave(){calls.sessionSaves++}
async function saveLayer(gid,btn,confirmed){calls.saveLayer.push({gid:gid,confirmed:confirmed,
 head:els[gid+'_head'].value,most:els[gid+'_most'].value})}
async function addRemedy(gid){calls.addRemedy.push({gid:gid,remedy:els[gid+'_nr_remedy'].value,
 dosage:els[gid+'_nr_dosage'].value,frequency:els[gid+'_nr_frequency'].value,
 timing:els[gid+'_nr_timing'].value,confirmed:voiceConfirmed(gid)})}
var answers={};
async function post(url,body){calls.post.push({url:url,body:body});
 if(url.indexOf('/match')>=0)return answers[body.text]||{ok:true,value:body.text,matched:false};
 if(url.indexOf('/layer-balanced')>=0)return {ok:true,updated:2,balanced:body.balanced};
 return {ok:true}}
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
function msg(ws,text,final){ws.onmessage({data:JSON.stringify({type:'Results',is_final:!!final,
 channel:{alternatives:[{transcript:text}]}})})}
"""

DUMP = ("process.stdout.write(JSON.stringify({calls:calls,sess:els.sessText.value,"
        "vals:Object.keys(els).reduce(function(o,k){o[k]=els[k].value;return o},{}),"
        "unmatched:Object.keys(els).filter(function(k){return els[k].classList.contains('unmatched')}),"
        "voice:Object.keys(els).reduce(function(o,k){if(els[k].dataset.voice!==undefined)"
        "o[k]=els[k].dataset.voice;return o},{}),"
        "checked:els.g0_balanced.checked,focused:document.activeElement&&document.activeElement.id,"
        "cardBalanced:!!cards.g0._cls.lbalanced,stat:els.astat.textContent,"
        "rfOpen:_rf!==null,sockets:sockets.length,"
        "micOn:Object.keys(mics).filter(function(k){return mics[k].classList.contains('on')})}));")


def _run(scenario):
    script = HARNESS + _page_js() + "\n(async function(){\n" + scenario + "\n})()" \
        ".catch(function(e){console.error(e);process.exit(1)});\n"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _speak(input_id, kind, *messages):
    """recField on one input, then the socket messages, then wait for the fill."""
    lines = [f"var me=await recField('{input_id}','{kind}');var ws=me.ws;ws.onopen();"]
    for text, final in messages:
        lines.append(f"msg(ws,{json.dumps(text)},{'true' if final else 'false'});")
    lines.append("await me.heard;")
    return "\n".join(lines)


def test_one_final_result_fills_one_field_only():
    r = _run("""
      answers['liver congestion']={ok:true,value:'Liver Congestion',matched:true,also_tail:false};
    """ + _speak("g0_head", "head", ("liver", False), ("liver congestion", True),
                 ("and something after", True)) + DUMP)
    v = r["vals"]
    assert v["g0_head"] == "Liver Congestion"
    assert v["g0_most"] == "" and v["gnew_head"] == "" and v["g0_nr_remedy"] == ""
    # Only the first final result was matched: one /match call, the later one ignored.
    assert [p["body"] for p in r["calls"]["post"]] == [{"kind": "head", "text": "liver congestion"}]
    # The stream stopped at that result: socket closed, mic tracks stopped, mic light off.
    assert r["calls"]["wsClosed"] == 1 and r["calls"]["tracksStopped"] == 1
    assert r["rfOpen"] is False and r["micOn"] == []
    # The words joined the full transcript.
    assert r["sess"] == "liver congestion" and r["calls"]["sessionSaves"] == 1
    # A matched Head on an existing card saves the card confirmed.
    assert r["calls"]["saveLayer"] == [{"gid": "g0", "confirmed": 1,
                                        "head": "Liver Congestion", "most": ""}]
    assert r["unmatched"] == [] and r["voice"] == {"g0_head": "1"}


def test_unmatched_words_stay_with_the_unmatched_class_and_save_unconfirmed():
    r = _run("""
      els.sessText.value='earlier words';
      answers['zzqx florp']={ok:true,value:'Zzqx Florp',matched:false,also_tail:false};
    """ + _speak("g0_most", "tail", ("zzqx florp", True)) + DUMP)
    assert r["vals"]["g0_most"] == "Zzqx Florp"
    assert r["unmatched"] == ["g0_most"]
    assert r["voice"] == {"g0_most": "0"}
    assert r["calls"]["saveLayer"][0]["confirmed"] == 0
    assert r["sess"] == "earlier words zzqx florp"


def test_clear_voice_removes_the_mark():
    r = _run("""
      answers['zzqx']={ok:true,value:'Zzqx',matched:false};
    """ + _speak("gnew_head", "head", ("zzqx", True)) + """
      // Glen types into the field: the page's input listener clears the mark.
      listeners.input({target:els.gnew_head});
    """ + DUMP)
    assert r["unmatched"] == [] and r["voice"] == {}


def test_head_and_tail_fills_both_fields():
    r = _run("""
      answers['head and tail liver']={ok:true,value:'Liver',matched:true,also_tail:true};
    """ + _speak("g0_head", "head", ("head and tail liver", True)) + DUMP)
    assert (r["vals"]["g0_head"], r["vals"]["g0_most"]) == ("Liver", "Liver")
    assert r["calls"]["saveLayer"] == [{"gid": "g0", "confirmed": 1, "head": "Liver",
                                        "most": "Liver"}]


def test_new_layer_head_fills_without_saving_and_carries_confirmed_to_add():
    r = _run("""
      answers['kidney']={ok:true,value:'Kidney',matched:true,also_tail:false};
      answers['florp 10 drops twice a day']={ok:true,value:'Florp',matched:false,
        dose:'10 drops',frequency:'twice a day',
        dosing:{dosage:'10 drops',frequency:'twice a day',timing:''}};
    """ + _speak("gnew_head", "head", ("kidney", True)) + """
      var me2=await recField('gnew_nr_remedy','remedy');me2.ws.onopen();
      msg(me2.ws,'florp 10 drops twice a day',true);await me2.heard;
    """ + DUMP)
    assert r["calls"]["saveLayer"] == []            # no rows yet on the new card
    add = r["calls"]["addRemedy"]
    assert len(add) == 1
    # Head matched but the remedy did not: the new layer goes in for review.
    assert add[0] == {"gid": "gnew", "remedy": "Florp", "dosage": "10 drops",
                      "frequency": "twice a day", "timing": "", "confirmed": 0}
    assert r["unmatched"] == ["gnew_nr_remedy"]


def test_matched_remedy_with_no_dose_spoken_takes_the_catalogue_default():
    r = _run("""
      els.g0_nr_timing.value='typed timing';
      answers['sulfur syntropy']={ok:true,value:'Sulfur Syntropy',matched:true,dose:'',frequency:'',
        dosing:{dosage:'1 capsule',frequency:'twice a day',timing:'with food, or as guided.'}};
    """ + _speak("g0_nr_remedy", "remedy", ("sulfur syntropy", True)) + DUMP)
    add = r["calls"]["addRemedy"][0]
    assert add == {"gid": "g0", "remedy": "Sulfur Syntropy", "dosage": "1 capsule",
                   "frequency": "twice a day", "timing": "typed timing", "confirmed": 1}


def test_saying_balanced_ticks_the_card_and_focuses_the_next_head():
    r = _run("""
      answers['balanced']={ok:true,value:'',matched:true,balanced:true};
    """ + _speak("g0_head", "head", ("balanced", True)) + DUMP)
    assert r["checked"] is True and r["cardBalanced"] is True
    assert r["focused"] == "gnew_head"
    lb = [p for p in r["calls"]["post"] if "layer-balanced" in p["url"]]
    assert lb == [{"url": "/author/a7/layer-balanced", "body": {"rids": ["5", "6"],
                                                                 "balanced": True}}]
    assert r["vals"]["g0_head"] == "" and r["calls"]["saveLayer"] == []


def test_tapping_the_same_mic_again_stops_without_filling():
    r = _run("""
      var me=await recField('g0_head','head');me.ws.onopen();
      var again=await recField('g0_head','head');
    """ + DUMP)
    assert r["rfOpen"] is False and r["stat"] == "Stopped."
    assert r["calls"]["post"] == [] and r["vals"]["g0_head"] == ""
