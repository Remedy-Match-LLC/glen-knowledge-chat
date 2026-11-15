"""The editor's terrain saves (review round 1, 2026-11-14).

1. Saves go through one promise chain, so the server applies them in the order typed.
2. After a save the page renders what the server STORED (the response), not the inputs.
   An input Glen is in, or one with a newer queued change, is left alone.
3. A failed save says "Not saved" for that field, keeps the bad class, and Regulation
   comes from the last stored state, never from the unsaved input.

Runs the page's own JS under node with a fake DOM and a fake server.
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
    out = []
    for name in ("regulationLevel", "terrainSave"):
        m = re.search(r"// %s:begin\n(.*?)// %s:end" % (name, name), _AUTHOR_JS, re.S)
        assert m, f"{name} markers missing from _AUTHOR_JS"
        out.append(m.group(1))
    return "\n".join(out).replace("__TID__", "a7")


HARNESS = r"""
var window=globalThis;
function El(id,v){return {id:id,value:v,textContent:'',_bad:false,
 classList:{add:function(){this._o._bad=true},remove:function(){this._o._bad=false}}}}
var els={};
['t_phase','t_location','bsi_a','bsi_b','bsi_c','bsi_d','t_regulation','t_warn','tstat']
 .forEach(function(id){var e=El(id,'');e.classList._o=e;els[id]=e});
var document={activeElement:null,getElementById:function(id){return els[id]||null}};
var server={log:[],inFlight:0,maxInFlight:0,stored:{phase:null,location:'',bsi:[null,null,null,null]},
 fail:{},reject:{},delay:{}};
var cols={bsi_a:0,bsi_b:1,bsi_c:2,bsi_d:3};
function post(url,body){
 server.inFlight++;server.maxInFlight=Math.max(server.maxInFlight,server.inFlight);
 var key=body.field+'='+body.value,d=server.delay[key]||0;
 server.log.push({start:key,bsi_a_input:els.bsi_a.value});
 return new Promise(function(res,rej){setTimeout(function(){
  server.inFlight--;server.log.push({done:key});
  if(server.reject[key]){rej(new Error('network'));return}
  if(server.fail[key]){res({ok:false,error:server.fail[key]});return}
  var s=server.stored;
  if(body.field in cols)s.bsi[cols[body.field]]=body.value===''?null:Number(body.value);
  else if(body.field==='phase')s.phase=body.value===''?null:Number(body.value);
  else s.location=body.value;
  res(Object.assign({ok:true},JSON.parse(JSON.stringify(s))))},d)})}
"""


def _run(scenario):
    script = HARNESS + _page_js() + "\n(async function(){\n" + scenario + "\n})()" \
        ".catch(function(e){console.error(e);process.exit(1)});\n"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


DUMP = ("process.stdout.write(JSON.stringify({log:server.log,max:server.maxInFlight,"
        "stored:window._tStored,server:server.stored,"
        "inputs:{a:els.bsi_a.value,b:els.bsi_b.value,c:els.bsi_c.value,d:els.bsi_d.value,"
        "phase:els.t_phase.value,location:els.t_location.value},"
        "bad:{a:els.bsi_a._bad,c:els.bsi_c._bad},"
        "reg:els.t_regulation.textContent,warn:els.t_warn.textContent,stat:els.tstat.textContent}));")


def test_rapid_saves_to_one_field_reach_the_server_in_order():
    # The first save is slow and the second fast: without the chain, 6 lands before 5.
    r = _run("""
      server.delay['bsi_a=5']=60;
      els.bsi_a.value='5';saveTerrain('bsi_a',els.bsi_a);
      els.bsi_a.value='6';await saveTerrain('bsi_a',els.bsi_a);
    """ + DUMP)
    assert [e.get("start") or "done:" + e["done"] for e in r["log"]] == \
        ["bsi_a=5", "done:bsi_a=5", "bsi_a=6", "done:bsi_a=6"]
    assert r["max"] == 1
    assert r["server"]["bsi"][0] == 6 and r["inputs"]["a"] == "6"


def test_saves_across_fields_are_serialized_too():
    r = _run("""
      server.delay['bsi_a=13']=40;server.delay['bsi_b=8']=20;
      els.bsi_a.value='13';saveTerrain('bsi_a',els.bsi_a);
      els.bsi_b.value='8';saveTerrain('bsi_b',els.bsi_b);
      els.bsi_c.value='5';saveTerrain('bsi_c',els.bsi_c);
      els.bsi_d.value='3';await saveTerrain('bsi_d',els.bsi_d);
    """ + DUMP)
    assert [e["start"] for e in r["log"] if "start" in e] == \
        ["bsi_a=13", "bsi_b=8", "bsi_c=5", "bsi_d=3"]
    assert r["max"] == 1
    assert r["reg"] == "Level 4 · Positive"


def test_the_page_renders_the_stored_state_from_the_response():
    # The server already holds a reading the page has not shown: the response wins.
    r = _run("""
      server.stored={phase:3,location:'kidney',bsi:[13,8,5,null]};
      els.bsi_b.value='99';els.t_location.value='typed but not saved';
      els.bsi_d.value='3';await saveTerrain('bsi_d',els.bsi_d);
    """ + DUMP)
    assert r["inputs"] == {"a": "13", "b": "8", "c": "5", "d": "3", "phase": "3",
                           "location": "kidney"}
    assert r["stored"] == {"phase": 3, "location": "kidney", "bsi": [13, 8, 5, 3]}
    assert r["reg"] == "Level 4 · Positive" and r["stat"] == "Saved."


def test_a_focused_input_is_not_overwritten():
    r = _run("""
      server.stored={phase:null,location:'',bsi:[13,8,5,null]};
      els.bsi_b.value='9';document.activeElement=els.bsi_b;
      els.bsi_d.value='3';await saveTerrain('bsi_d',els.bsi_d);
    """ + DUMP)
    assert r["inputs"]["b"] == "9"          # Glen is in it
    assert r["inputs"]["a"] == "13"         # the others follow the response
    assert r["reg"] == "Level 4 · Positive"   # from stored B = 8, never the typed 9


def test_an_input_with_a_newer_queued_change_is_not_overwritten():
    # When response 1 (bsi_a=5) renders, bsi_a=6 is still queued: the input keeps 6.
    r = _run("""
      els.bsi_a.value='5';saveTerrain('bsi_a',els.bsi_a);
      els.bsi_a.value='6';await saveTerrain('bsi_a',els.bsi_a);
    """ + DUMP)
    second = [e for e in r["log"] if e.get("start") == "bsi_a=6"][0]
    assert second["bsi_a_input"] == "6"
    assert r["inputs"]["a"] == "6"


def test_a_failed_save_says_not_saved_and_regulation_uses_stored_state():
    r = _run("""
      window._tStored={phase:null,location:'',bsi:[13,8,5,3]};
      server.stored={phase:null,location:'',bsi:[13,8,5,3]};
      server.fail['bsi_c=22']='each BSI number must be 1 to 21';
      els.bsi_a.value='13';els.bsi_b.value='8';els.bsi_d.value='3';
      els.bsi_c.value='22';await saveTerrain('bsi_c',els.bsi_c);
    """ + DUMP)
    assert r["stat"].startswith("Not saved: BSI C")
    assert r["bad"]["c"] is True
    assert r["inputs"]["c"] == "22"         # the unsaved value stays for Glen to fix
    assert r["reg"] == "Level 4 · Positive"   # from stored C = 5
    assert r["stored"]["bsi"] == [13, 8, 5, 3]


def test_a_network_error_is_not_saved_and_the_chain_keeps_going():
    r = _run("""
      window._tStored={phase:null,location:'',bsi:[13,8,5,3]};
      server.stored={phase:null,location:'',bsi:[13,8,5,3]};
      server.reject['bsi_c=9']=true;
      els.bsi_c.value='9';saveTerrain('bsi_c',els.bsi_c);
      els.bsi_d.value='2';await saveTerrain('bsi_d',els.bsi_d);
    """ + DUMP)
    # The second save still ran after the first failed, and its response rendered.
    assert [e["start"] for e in r["log"] if "start" in e] == ["bsi_c=9", "bsi_d=2"]
    assert r["server"]["bsi"] == [13, 8, 5, 2]
    assert r["inputs"]["c"] == "5"          # re-rendered from the stored state
    assert r["reg"] == "Level 4 · Positive"


def test_a_failure_on_its_own_leaves_the_bad_class_and_stored_regulation():
    r = _run("""
      window._tStored={phase:null,location:'',bsi:[13,8,5,3]};
      server.reject['bsi_c=9']=true;
      els.bsi_c.value='9';await saveTerrain('bsi_c',els.bsi_c);
    """ + DUMP)
    assert r["stat"].startswith("Not saved: BSI C")
    assert r["bad"]["c"] is True and r["inputs"]["c"] == "9"
    assert r["reg"] == "Level 4 · Positive"
