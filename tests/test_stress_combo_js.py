"""The sectioned stress drop-down on the editor page (locCombo), run under node.

Glen, 15 November 2026: "SPIRIT ... then a space, then MIND ... then another space, then
BODY ... then another space before the full list", for every stress input ("all"), and
"Add a section to add stresses to the SPIRIT stress list". Runs the page's own locCombo
block with a small fake DOM, as tests/test_layer_handsfree_js.py does.
"""
import json
import re
import shutil
import subprocess

import pytest

from dashboard.biofield_report_html import _AUTHOR_JS, render_author_html

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    not NODE, reason="node is not installed on this machine, so the page JS cannot be run")


def _block():
    m = re.search(r"// locCombo:begin\n(.*?)// locCombo:end", _AUTHOR_JS, re.S)
    assert m, "locCombo markers missing from _AUTHOR_JS"
    return m.group(1).replace("__TID__", "a7")


HARNESS = r"""
var window=globalThis;
function Event(t){this.type=t}
function El(tag,id){var e={tagName:tag,id:id||'',value:'',innerHTML:'',hidden:false,style:{},
 dataset:{},_attrs:{},_ls:[],children:[],isConnected:true};
 e.setAttribute=function(k,v){e._attrs[k]=String(v)};e.getAttribute=function(k){return k in e._attrs?e._attrs[k]:null};
 e.removeAttribute=function(k){delete e._attrs[k]};e.hasAttribute=function(k){return k in e._attrs};
 e.addEventListener=function(t,fn,cap){e._ls.push({t:t,fn:fn,cap:!!cap})};
 e.appendChild=function(c){e.children.push(c);return c};
 e.getBoundingClientRect=function(){return {left:10,bottom:30,width:200}};
 e.dispatchEvent=function(ev){var stop=false;ev.target=ev.target||e;
  ev.preventDefault=ev.preventDefault||function(){ev.defaultPrevented=true};
  ev.stopImmediatePropagation=function(){stop=true};
  [true,false].forEach(function(cap){e._ls.forEach(function(l){
   if(!stop&&l.t===ev.type&&l.cap===cap)l.fn(ev)})});return !ev.defaultPrevented};
 return e}
var els={},inputs=[];
function mkInput(id,attrs){var e=El('INPUT',id);Object.keys(attrs||{}).forEach(function(k){e.setAttribute(k,attrs[k])});
 els[id]=e;inputs.push(e);return e}
var body=El('BODY');
var document={body:body,documentElement:body,getElementById:function(id){return els[id]||null},
 createElement:function(t){return El(t.toUpperCase())},
 querySelectorAll:function(sel){return sel==='input[data-loccombo]'?inputs.filter(function(i){return i.hasAttribute('data-loccombo')}):[]}};
var calls={post:[],fetch:[],loadStress:0,changes:[],inputs:[],ownEnter:[]};
var SERVER={spirit:['Grief','Pressure 2'],mind:[],body:['ED4: Nerve','Lead'],
 all:['Adrenal','ED4: Nerve','Grief','Liver'],errors:{},unnamed:[]};
async function fetch(u){calls.fetch.push(u);return {json:async function(){return JSON.parse(JSON.stringify(SERVER))}}}
var POST_ANSWER={};
async function post(u,b){calls.post.push({url:u,body:b});
 var a=POST_ANSWER[b.label]||{ok:true,label:b.label};
 if(a.ok&&a.spirit)SERVER.spirit=SERVER.spirit.concat([a.label]);return a}
function loadStress(){calls.loadStress++}
function key(el,k){var ev=new Event('keydown');ev.key=k;el.dispatchEvent(ev);return ev}
function fire(el,t){el.dispatchEvent(new Event(t))}
function mouse(lb,i){var t={dataset:i==null?{}:{i:String(i)}};t.closest=function(){return i==null?null:t};
 var ev=new Event('mousedown');ev.target=t;lb.dispatchEvent(ev);return ev}
function rowsText(rows){return rows.map(function(r){return r.kind+(r.text?':'+r.text:'')})}
"""


def _run(script):
    src = HARNESS + _block() + "\n(async function(){\n" + script + "\n})().catch(function(e){" \
        "console.log(JSON.stringify({error:String(e&&e.stack||e)}))});"
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert "error" not in got, got.get("error")
    return got


SECS = ("{spirit:['Pressure 10','Grief','Pressure 2'],mind:[],body:['ED4: Nerve'],"
        "all:['Adrenal','Grief','Liver'],errors:{}}")


@needs_node
def test_sections_in_order_with_headings_and_gaps():
    got = _run(f"console.log(JSON.stringify(rowsText(locRows({SECS},''))))")
    assert got == ["head:SPIRIT", "opt:Pressure 10", "opt:Grief", "opt:Pressure 2", "gap",
                   "head:MIND", "none:none yet", "gap",
                   "head:BODY", "opt:ED4: Nerve", "gap",
                   "head:ALL STRESSES", "opt:Adrenal", "opt:Grief", "opt:Liver"]


@needs_node
def test_the_page_keeps_the_servers_order():
    """The server sorts (natural_key). The page must not reorder: Pressure 10 stays first."""
    got = _run(f"console.log(JSON.stringify(rowsText(locRows({SECS},'pre'))))")
    assert got == ["head:SPIRIT", "opt:Pressure 10", "opt:Pressure 2", "gap"]


@needs_node
def test_typing_filters_every_section_and_drops_a_heading_with_no_match():
    got = _run(f"""console.log(JSON.stringify({{
      gr:rowsText(locRows({SECS},'gr')),
      ed:rowsText(locRows({SECS},'ed4 n')),
      none:rowsText(locRows({SECS},'zzz'))}}))""")
    assert got["gr"] == ["head:SPIRIT", "opt:Grief", "gap", "head:ALL STRESSES", "opt:Grief"]
    # Punctuation is ignored, so "ed4 n" finds "ED4: Nerve".
    assert got["ed"] == ["head:BODY", "opt:ED4: Nerve", "gap"]
    assert got["none"] == ["none:no match"]


@needs_node
def test_an_unreadable_section_says_so():
    got = _run("console.log(JSON.stringify(rowsText(locRows({spirit:[],mind:[],body:[],all:[],"
               "errors:{body:'no PDF'}},''))))")
    assert "none:could not be read" in got and got.count("none:none yet") == 2


@needs_node
def test_keys_pick_only_items_and_fire_input_then_change():
    got = _run("""
      var inp=mkInput('t_location',{'data-loccombo':'',list:'vocab'});
      inp.addEventListener('input',function(){calls.inputs.push(inp.value)});
      inp.addEventListener('change',function(){calls.changes.push(inp.value)});
      // The input's own Enter handler (an inline onkeydown), registered before the combo.
      inp.addEventListener('keydown',function(e){if(e.key==='Enter')calls.ownEnter.push(inp.value)});
      LOC_SECTIONS={spirit:['Grief'],mind:[],body:['ED4: Nerve'],all:['Adrenal'],errors:{}};
      locInit();
      var c=LOC_COMBOS[0],lb=c.lb;
      fire(inp,'focus');
      var opened={open:c.open,expanded:inp.getAttribute('aria-expanded'),hidden:lb.hidden,
        html:lb.innerHTML,list:inp.hasAttribute('list'),role:inp.getAttribute('role'),
        lbrole:lb.getAttribute('role'),inBody:body.children.indexOf(lb)>=0};
      key(inp,'ArrowDown');var first=c.rows[c.active].text;
      key(inp,'ArrowDown');var second=c.rows[c.active].text;
      key(inp,'ArrowUp');key(inp,'ArrowUp');var top=c.rows[c.active].text;
      var ev=key(inp,'Enter');
      console.log(JSON.stringify({opened:opened,first:first,second:second,top:top,
        value:inp.value,changes:calls.changes,inputs:calls.inputs,open:c.open,
        prevented:!!ev.defaultPrevented,ownEnter:calls.ownEnter,
        act:inp.getAttribute('aria-activedescendant')}));""")
    o = got["opened"]
    assert o["open"] and o["expanded"] == "true" and not o["hidden"] and o["inBody"]
    assert o["role"] == "combobox" and o["lbrole"] == "listbox" and not o["list"]
    html = o["html"]
    assert html.index(">SPIRIT<") < html.index(">Grief<") < html.index("class=locgap") \
        < html.index(">MIND<") < html.index(">none yet<") < html.index(">BODY<") \
        < html.index(">ED4: Nerve<") < html.index(">ALL STRESSES<") < html.index(">Adrenal<")
    assert html.count("role=option") == 3 and "class=lochead role=presentation" in html
    assert (got["first"], got["second"], got["top"]) == ("Grief", "ED4: Nerve", "Grief")
    assert got["value"] == "Grief" and got["changes"] == ["Grief"] and got["inputs"] == ["Grief"]
    assert not got["open"] and got["prevented"] and got["act"] is None
    # Enter on a highlighted item picks it; the input's own Enter (add) does not run.
    assert got["ownEnter"] == []


@needs_node
def test_free_text_enter_reaches_the_inputs_own_handler_and_escape_closes():
    got = _run("""
      var inp=mkInput('x',{'data-loccombo':''});
      inp.addEventListener('keydown',function(e){if(e.key==='Enter')calls.ownEnter.push(inp.value)});
      LOC_SECTIONS={spirit:['Grief'],mind:[],body:[],all:[],errors:{}};
      locInit();var c=LOC_COMBOS[0];
      fire(inp,'focus');inp.value='Something new';fire(inp,'input');
      var openAfterTyping=c.open,html=c.lb.innerHTML;
      var ev=key(inp,'Enter');
      fire(inp,'focus');key(inp,'Escape');
      console.log(JSON.stringify({openAfterTyping:openAfterTyping,html:html,own:calls.ownEnter,
        prevented:!!ev.defaultPrevented,open:c.open,value:inp.value,changes:calls.changes}))""")
    assert got["openAfterTyping"] and "no match" in got["html"]
    assert got["own"] == ["Something new"] and not got["prevented"]
    assert not got["open"] and got["value"] == "Something new" and got["changes"] == []


@needs_node
def test_a_click_picks_an_item_and_a_heading_is_not_picked():
    got = _run("""
      var inp=mkInput('g0_head',{'data-loccombo':''});
      inp.addEventListener('change',function(){calls.changes.push(inp.value)});
      LOC_SECTIONS={spirit:['Grief'],mind:[],body:[],all:['Liver'],errors:{}};
      locInit();var c=LOC_COMBOS[0];fire(inp,'focus');
      var headIdx=c.rows.findIndex(function(r){return r.kind==='head'});
      mouse(c.lb,null);var afterHead=inp.value;
      c.pick(headIdx);var afterPickHead=inp.value;
      var liver=c.rows.findIndex(function(r){return r.text==='Liver'});
      mouse(c.lb,liver);
      console.log(JSON.stringify({afterHead:afterHead,afterPickHead:afterPickHead,
        value:inp.value,changes:calls.changes,open:c.open}))""")
    assert got["afterHead"] == "" and got["afterPickHead"] == ""
    assert got["value"] == "Liver" and got["changes"] == ["Liver"] and not got["open"]


@needs_node
def test_init_is_once_per_input_and_catches_inputs_added_later():
    got = _run("""
      mkInput('a',{'data-loccombo':''});locInit();locInit();var n1=LOC_COMBOS.length;
      mkInput('b',{'data-loccombo':''});mkInput('remedy',{list:'catalog'});locInit();
      console.log(JSON.stringify({n1:n1,n2:LOC_COMBOS.length,
        ids:LOC_COMBOS.map(function(c){return c.inp.id}),
        remedyList:els.remedy.getAttribute('list')}))""")
    assert got == {"n1": 1, "n2": 2, "ids": ["a", "b"], "remedyList": "catalog"}


@needs_node
def test_add_spirit_stress():
    got = _run("""
      var inp=mkInput('spiritAdd',{'data-loccombo':''});els.spiritStat=El('SPAN','spiritStat');
      locInit();await locLoad();
      var out={};
      await spiritAddNow();out.empty=els.spiritStat.textContent;
      inp.value='grief';await spiritAddNow();out.dup=els.spiritStat.textContent;out.dupPosts=calls.post.length;
      POST_ANSWER['Fear']={ok:true,label:'Fear',spirit:true};
      inp.value='Fear';await spiritAddNow();
      out.added=els.spiritStat.textContent;out.cleared=inp.value;out.spirit=LOC_SECTIONS.spirit;
      out.post=calls.post[0];out.loadStress=calls.loadStress;
      // Enter with nothing highlighted adds too.
      POST_ANSWER['Joy']={ok:true,label:'Joy',spirit:true};
      inp.value='Joy';LOC_COMBOS[0].show();key(inp,'Enter');
      await new Promise(function(r){setTimeout(r,20)});out.enter=els.spiritStat.textContent;
      // Merged into a stress from another source: nothing new in SPIRIT, and it says so.
      POST_ANSWER['Lead']={ok:true,label:'Lead'};
      inp.value='Lead';await spiritAddNow();out.other=els.spiritStat.textContent;
      POST_ANSWER['Bad']={ok:false,error:'empty label'};
      inp.value='Bad';await spiritAddNow();out.bad=els.spiritStat.textContent;
      console.log(JSON.stringify(out))""")
    assert got["empty"] == "Type or pick a stress first. Nothing added."
    assert got["dup"] == "Already in SPIRIT: grief. Nothing added." and got["dupPosts"] == 0
    assert got["post"] == {"url": "/author/a7/stress/add", "body": {"label": "Fear"}}
    assert got["added"] == "Added to SPIRIT: Fear" and got["cleared"] == ""
    assert "Fear" in got["spirit"] and got["loadStress"] == 1
    assert got["enter"] == "Added to SPIRIT: Joy"
    assert got["other"] == "Already a stress on this test outside SPIRIT: Lead. Nothing added."
    assert got["bad"] == "Not added: empty label"


# ---- the page carries it on every stress input -----------------------------------------

def _layer(rid, layer, head, tail, remedy):
    return {"rid": rid, "id": rid, "layer": layer, "stored_layer": layer, "head": head,
            "most_affected": tail, "remedy": remedy, "dosage": "", "frequency": "",
            "timing": "", "confirmed": 1, "origin": "live", "zone": "top",
            "layer_balanced": 0}


def _page():
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": [_layer(1, 1, "Liver", "Gut", "Sobopla")], "schedule": []}
    return render_author_html(rep, [], "", clinical_checklist=[
        {"label": "Fatigue", "stress_pattern": "Adrenal exhaustion", "checked": False}])


def test_every_stress_input_gets_the_drop_down_and_remedy_inputs_do_not():
    h = _page()
    for iid in ("t_location", "bsi_location", "spiritAdd", "g0_head", "g0_most",
                "gnew_head", "gnew_most"):
        m = re.search(r"<input id=%s [^>]*>" % iid, h)
        assert m and " data-loccombo" in m.group(0) and " list=" not in m.group(0), iid
    assert re.search(r"<input class=stress-add data-loccombo ", h)
    assert re.search(r"<input class=clinical-stress data-loccombo ", h)
    assert "list=vocab" not in h and 'list="vocab"' not in h
    assert "<datalist id=vocab>" not in h and "getElementById('vocab')" not in h
    assert 'id="r1_remedy" class=rem list=catalog' in h
    assert "<input id=g0_nr_remedy class=rem list=catalog" in h
    assert "data-loccombo" not in "".join(re.findall(r"<input[^>]*list=catalog[^>]*>", h))
    assert "locInit();locWatch();" in h
    assert "/api/vocab?limit=5000&sections=1&test_id=a7" in h


def test_the_stress_panel_inputs_get_it_too():
    from dashboard.biofield_report_html import render_stress_panel
    html = render_stress_panel({"active": [], "balanced": [],
                                "by_layer": [{"layer": 1, "head": "Liver", "rids": [1],
                                              "stresses": []}], "unassigned": []})
    assert "class=stress-add data-loccombo" in html and "list=vocab" not in html


# ---- Glen, 15 Nov 2026: BODY's subheads and greyed stresses already on a layer -------

GROUPS = ("{spirit:['Liver Detox'],mind:[],body:['Skin & Gut','ED4: Nerve','Lead','Humility'],"
          "body_groups:[['Causal chain',['Skin & Gut']],['Infoceuticals',['ED4: Nerve']],"
          "['Body stresses',['Lead']],['Psyche',['Humility']]],"
          "on_layer:['Skin & Gut','liver detox'],all:['Lead','Skin & Gut'],errors:{}}")


@needs_node
def test_body_draws_its_subheads_in_the_servers_order():
    got = _run(f"console.log(JSON.stringify(rowsText(locRows({GROUPS},''))))")
    assert got == ["head:SPIRIT", "opt:Liver Detox", "gap",
                   "head:MIND", "none:none yet", "gap",
                   "head:BODY", "sub:Causal chain", "opt:Skin & Gut",
                   "sub:Infoceuticals", "opt:ED4: Nerve",
                   "sub:Body stresses", "opt:Lead", "sub:Psyche", "opt:Humility", "gap",
                   "head:ALL STRESSES", "opt:Lead", "opt:Skin & Gut"]


@needs_node
def test_a_subhead_with_no_match_is_dropped_while_typing():
    got = _run(f"console.log(JSON.stringify(rowsText(locRows({GROUPS},'hum'))))")
    assert got == ["head:BODY", "sub:Psyche", "opt:Humility", "gap"]


@needs_node
def test_without_groups_body_is_the_flat_list():
    got = _run("console.log(JSON.stringify(rowsText(locRows({spirit:[],mind:[],"
               "body:['Lead'],all:[],errors:{}},''))))")
    assert "head:BODY" in got and "opt:Lead" in got and not any(r.startswith("sub:") for r in got)


@needs_node
def test_stresses_on_a_layer_are_greyed_everywhere_and_still_pick():
    got = _run(f"""
      var rows=locRows({GROUPS},'');
      var done=rows.filter(function(r){{return r.done}}).map(function(r){{return r.sec+':'+r.text}});
      var inp=mkInput('t_location',{{'data-loccombo':''}});
      inp.addEventListener('change',function(){{calls.changes.push(inp.value)}});
      LOC_SECTIONS={GROUPS};
      locInit();var c=LOC_COMBOS[0];fire(inp,'focus');
      var html=c.lb.innerHTML;
      var i=c.rows.findIndex(function(r){{return r.kind==='opt'&&r.text==='Skin & Gut'}});
      mouse(c.lb,i);
      console.log(JSON.stringify({{done:done,html:html,value:inp.value,changes:calls.changes}}));""")
    assert got["done"] == ["spirit:Liver Detox", "body:Skin & Gut", "all:Skin & Gut"]
    assert got["html"].count("locopt locdone") == 3
    assert got["html"].count('title="Already on a layer"') == 3
    assert "class=locsub role=presentation>Causal chain<" in got["html"]
    assert got["value"] == "Skin & Gut" and got["changes"] == ["Skin & Gut"]


@needs_node
def test_a_layer_head_in_spirit_can_still_be_added_as_a_stress():
    # Review of #2292: a head shows in SPIRIT (spirit_heads) but is not a stored stress.
    got = _run("""
      SERVER.spirit=['Grief','Liver Detox'];SERVER.spirit_heads=['Liver Detox'];
      var inp=mkInput('spiritAdd',{'data-loccombo':''});els.spiritStat=El('SPAN','spiritStat');
      locInit();await locLoad();
      var real=post;
      post=async function(u,b){var a=await real(u,b);
        SERVER.spirit_heads=SERVER.spirit_heads.filter(function(h){return locNorm(h)!==locNorm(b.label)});
        return a};
      var out={};
      inp.value='liver detox';await spiritAddNow();
      out.added=els.spiritStat.textContent;out.posts=calls.post.length;
      inp.value='Grief';await spiritAddNow();out.dup=els.spiritStat.textContent;
      out.posts2=calls.post.length;
      console.log(JSON.stringify(out))""")
    assert got["posts"] == 1 and got["added"] == "Added to SPIRIT: liver detox"
    assert got["dup"] == "Already in SPIRIT: Grief. Nothing added." and got["posts2"] == 1
