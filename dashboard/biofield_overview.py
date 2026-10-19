"""Glen's intervention overview chart, a working view in the local app (2026-10-18).

Glen: "each row would be a proposed intervention ... sorted for the interventions with
the most + signs at the top", then "Show ratings, and rank by totalling them. Also show
the number of patterns supported", and a button to show "only the clinical patterns in
the biofield vs show all".

- Rows are the items ticked for the client, one per Plan line, so the approved merge
  map joins them as on the Plan. A cell is the highest overall rating among the
  line's entries under that pattern; blank means the item is not listed there.
- Groups, in order (Glen): Remedies (Functional Formulations, Infoceuticals, PRL,
  Fullscript), Healing Tools, Foods, Ingredients (Nutrients and Botanicals).
- Within a group, rows rank by total over the patterns shown, then the higher single
  rating, then name.
- A yellow minus flags a caution and never changes the total (Glen: "flag only, never
  subtract"; "yellow rather than red"). Sources: the approved ingredient caution table,
  and a food one ticked pattern emphasizes and another avoids. An item that lowers
  blood pressure is not flagged: no client's low pressure is recorded yet (Glen "no"
  to flagging it for everyone).
- Never printed for a client.
"""
import html
import json

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_plan_report as pr
from dashboard.biofield_clinical_checklist import _norm

GROUPS = ("Remedies", "Healing Tools", "Foods", "Ingredients")
GROUP_OF = {cs.FORMULATIONS: "Remedies", cs.INFOCEUTICALS: "Remedies", "prl": "Remedies",
            cs.FULLSCRIPT: "Remedies", cs.HEALING_TOOLS: "Healing Tools",
            "foods_emphasize": "Foods", "foods_avoid": "Foods",
            cs.NUTRIENTS: "Ingredients", cs.BOTANICALS: "Ingredients"}
KIND = {cs.FORMULATIONS: "Functional Formulation", cs.INFOCEUTICALS: "Infoceutical",
        "prl": "PRL", cs.FULLSCRIPT: "Fullscript", cs.HEALING_TOOLS: "",
        "foods_emphasize": "Emphasize", "foods_avoid": "Avoid",
        cs.NUTRIENTS: "Nutrient", cs.BOTANICALS: "Botanical"}


def _food_keys_by_item(cx):
    """{(condition key, section, name key): food keys} from the catalog."""
    return {(item_key, section, name_key): cs._food_keys(food_keys)
            for item_key, section, name_key, food_keys in cx.execute(
                "SELECT item_key,section,name_key,food_keys FROM biofield_clinical_section_item")}


def overview(cx, test_id, layers=()):
    """The chart's data: {"ba": [column], "all": [column], "rows": [row]}. A column is
    {"key", "label"}; a row is {"name", "kind", "group", "cells": {column key: rating},
    "cautions": [why], "conflicts": {column key: why}}."""
    groups = pr.plan(cx, test_id)
    facts = _food_keys_by_item(cx)
    caution_hits = {}
    for name, _text, hits in pr.cautions_for(groups, pr.stored_frame(cx)[1]):
        for line_name in hits:
            caution_hits.setdefault(line_name, []).append(name)
    labels, rows, avoided = {}, [], {}
    # Foods a ticked pattern avoids, by food key, for the emphasize rows' minus.
    for line in groups.get("foods_avoid") or []:
        for en in line["entries"]:
            keys = facts.get((en.get("condition_key"), "foods_avoid", _norm(en["name"])), [])
            labels.setdefault(_norm(en["condition"]), en["condition"])
            for key in keys or [_norm(en["name"])]:
                avoided.setdefault(key, []).append((_norm(en["condition"]), en["name"]))
    for section, lines in groups.items():
        for line in lines:
            cells, keys = {}, set()
            for en in line["entries"]:
                ck = _norm(en["condition"])
                labels.setdefault(ck, en["condition"])
                fk = facts.get((en.get("condition_key"), en.get("section", section),
                                _norm(en["name"])), [])
                keys.update(fk or [_norm(en["name"])])
                if en["overall"] is not None:
                    cells[ck] = max(cells.get(ck, 0), int(en["overall"]))
            cautions = [f"Caution: {n}" for n in caution_hits.get(line["name"], [])]
            conflicts = {}
            if section == "foods_emphasize":
                # The minus sits in the column of the pattern that avoids the food;
                # the food's own patterns are not a conflict with themselves.
                own = {_norm(en["condition"]) for en in line["entries"]}
                for key in sorted(keys):
                    for ck, other in avoided.get(key, []):
                        if ck not in own:
                            conflicts.setdefault(ck, f"{labels.get(ck, ck)} avoids {other}")
            rows.append({"name": line["name"], "kind": KIND.get(section, ""),
                         "group": GROUP_OF.get(section, "Remedies"), "cells": cells,
                         "cautions": cautions, "conflicts": conflicts})
    ba, seen = [], set()
    for layer in layers or []:
        head = str(layer.get("head") or "").strip()
        if head and _norm(head) not in seen:
            seen.add(_norm(head))
            ba.append({"key": _norm(head), "label": head})
    rest = sorted((k for k in labels if k not in seen), key=lambda k: labels[k].lower())
    all_cols = ba + [{"key": k, "label": labels[k]} for k in rest]
    return {"ba": ba, "all": all_cols, "rows": rows, "groups": list(GROUPS)}


def render_page(data, client_name="", date=""):
    """The chart as a page of its own. Every string is escaped in the page's script."""
    e = html.escape
    payload = json.dumps(data).replace("</", "<\\/")
    who = " · ".join(x for x in (client_name, date) if x)
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Intervention overview</title><style>" + CSS + "</style></head><body><main>"
            f"<h1>Intervention overview</h1><p class=who>{e(who)}</p>"
            "<p>Each row is an item ticked in the Clinical Summary, and each column is a "
            "clinical pattern. A cell is the item's overall rating there; blank means it is "
            "not listed under that pattern. Within each group, rows rank by total, then the "
            "highest single rating, then name. A yellow minus flags a caution and never "
            "changes the total; hover it for the reason. Not printed.</p>"
            "<button id=b type=button></button><div class=wrap><table id=t></table></div>"
            "<p id=off class=off></p></main>"
            f"<script>const D={payload};{SCRIPT}</script></body></html>")


CSS = (
    ":root{--bg:#fff;--fg:#1d2a33;--mut:#5d6b75;--line:#d5dde2;--head:#f2f5f7;--hi:#e3f0e6;"
    "--grp:#dfe8ee;--warn:#f5c518;--warn-fg:#1d2a33}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161b1f;--fg:#e4e9ec;"
    "--mut:#9aa7b0;--line:#33404a;--head:#20282e;--hi:#1f3a27;--grp:#2a3640}}"
    "body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}"
    "main{padding:16px}h1{font-size:20px;margin:0}p{color:var(--mut);margin:4px 0 12px;max-width:72ch}"
    "button{font:inherit;padding:6px 12px;border:1px solid var(--line);background:var(--head);"
    "color:var(--fg);border-radius:6px;cursor:pointer}.wrap{overflow-x:auto;margin-top:12px}"
    "table{border-collapse:collapse;min-width:600px}th,td{border:1px solid var(--line);"
    "padding:3px 6px;text-align:center}th{background:var(--head);font-weight:600;font-size:12px;"
    "vertical-align:bottom}td.n{text-align:left;white-space:nowrap}td.n small{color:var(--mut);"
    "margin-left:6px}td.t{font-weight:700}td.v{background:var(--hi)}tr.g td{background:var(--grp);"
    "text-align:left;font-weight:700;font-size:15px;padding:6px}.m{display:inline-block;"
    "background:var(--warn);color:var(--warn-fg);border-radius:3px;padding:0 5px;margin-left:4px;"
    "font-weight:700;cursor:help}"
)

SCRIPT = r"""
let v='ba';
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function minus(why){return '<span class=m title="'+esc(why)+'" aria-label="Caution: '+esc(why)+'">&minus;</span>'}
function draw(){
 if(!D.ba.length)v='all';
 const cols=D[v];const b=document.getElementById('b');
 b.textContent=v==='ba'?'Showing the Biofield Analysis patterns ('+cols.length+'). Show all ticked patterns':'Showing all ticked patterns ('+cols.length+'). Show the Biofield Analysis patterns only';
 b.hidden=!D.ba.length;
 let h='<tr><th>Intervention</th>'+cols.map(c=>'<th>'+esc(c.label)+'</th>').join('')+'<th>Total</th><th>Patterns</th></tr>';
 let off=0;
 for(const g of D.groups){
  const rows=D.rows.filter(r=>r.group===g).map(r=>{const vals=cols.map(c=>r.cells[c.key]);const got=vals.filter(x=>x!=null);
   return {r,vals,tot:got.reduce((a,b)=>a+b,0),n:got.length,mx:Math.max(0,...got)}});
  const shown=rows.filter(x=>x.n>0);off+=rows.length-shown.length;
  shown.sort((a,b)=>b.tot-a.tot||b.mx-a.mx||a.r.name.localeCompare(b.r.name));
  if(!shown.length)continue;
  h+='<tr class=g><td colspan='+(cols.length+3)+'>'+esc(g)+' ('+shown.length+')</td></tr>';
  for(const x of shown){
   h+='<tr><td class=n>'+esc(x.r.name)+(x.r.kind?'<small>'+esc(x.r.kind)+'</small>':'')+(x.r.cautions.length?minus(x.r.cautions.join('; ')):'')+'</td>';
   h+=cols.map((c,i)=>{const s=x.vals[i];const cf=x.r.conflicts[c.key];
    if(s==null)return '<td>'+(cf?minus(cf):'')+'</td>';
    return '<td class=v>'+(s>=10?'X':s)+(cf?minus(cf):'')+'</td>'}).join('');
   h+='<td class=t>'+x.tot+'</td><td>'+x.n+'</td></tr>'}}
 document.getElementById('t').innerHTML=h;
 document.getElementById('off').textContent=off?off+' ticked item'+(off===1?' has':'s have')+' no rating under the patterns shown, so '+(off===1?'it is':'they are')+' left off this view.':'';
}
document.getElementById('b').onclick=()=>{v=v==='ba'?'all':'ba';draw()};draw();
"""
