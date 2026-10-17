"""Glen, 2026-10-16: "If I check a remedy in a category, it automatically checks the
category, too, which is fine. But if I uncheck all remedies in the category, it
doesn't uncheck the category."

After a reload a ticked row was drawn as covered by a layer ("done"), and the page
will not untick a covered row. Ticks alone must leave it merely selected.
"""
from dashboard.biofield_report_html import render_clinical_checklist


def _row(html, label):
    start = html.index(f'data-label="{label}"')
    head = html.rindex("<div class='", 0, start)
    return html[head:html.index("clinical-remove", start)]


def test_a_row_ticked_by_its_remedies_is_selected_not_done():
    html = render_clinical_checklist([{
        "label": "Fatigue", "checked": True, "covered_by": "",
        "selection_saved": True, "selected_remedies": ["Neuro Magnesium"],
        "common_remedies": ["Neuro Magnesium"]}])
    row = _row(html, "Fatigue")
    assert row.startswith("<div class='clinical-item selected'")
    assert "class=clinical-check type=checkbox" in row and " checked onchange=toggleClinicalItem" in row
    assert "Needs remedy coverage" in row


def test_a_row_covered_by_the_chain_is_still_done():
    html = render_clinical_checklist([{
        "label": "Cataract", "checked": True, "covered_by": "OcuHeal Eye Drops",
        "layer": 2, "common_remedies": ["OcuHeal Eye Drops"]}],
        layers=[{"layer": 2, "head": "Lens", "remedy": "OcuHeal Eye Drops"}])
    row = _row(html, "Cataract")
    assert row.startswith("<div class='clinical-item done'")
    assert "Layer 2 · OcuHeal Eye Drops" in row


def test_the_untick_script_unticks_a_row_that_is_not_done():
    """The browser rule this relies on: with no remedy ticked, a row that is not
    'done' is unticked."""
    from dashboard import biofield_report_html as h
    src = open(h.__file__).read()
    assert ("if(check&&!row.classList.contains('done')){check.checked=remedies.length>0}"
            in src)


def test_unticking_the_row_by_hand_unticks_and_saves_its_remedies():
    """Glen: "and then I can't manually uncheck the category either." Runs the page's
    own two handlers in node against a fake row."""
    import json
    import os
    import re
    import shutil
    import subprocess
    import pytest
    from dashboard import biofield_report_html as h
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI for this test")
        pytest.skip("needs node")
    src = open(h.__file__).read()
    fns = ""
    for name in ("toggleClinicalItem", "selectClinicalRemedy"):
        m = re.search(r"(?:async )?function " + name + r"\(box\)\{.*?\n\}", src, re.S) or \
            re.search(r"async function " + name + r"\(box\)\{.*?Could not save remedy selection'\}", src, re.S)
        assert m, name
        fns += m.group(0).replace("__TID__", "a1") + "\n"
    harness = r"""
var classes={done:false,selected:true};
var remedies=[{value:'Neuro Magnesium',checked:true},{value:'Calm',checked:true}];
var check={checked:false};
var row={dataset:{label:'Fatigue'},classList:{contains:function(c){return !!classes[c]},
 toggle:function(c,v){classes[c]=v}},
 querySelectorAll:function(sel){return sel.indexOf(':checked')>=0?
  remedies.filter(function(r){return r.checked}):remedies},
 querySelector:function(sel){return sel==='.clinical-check'?check:null}};
var box={checked:false,closest:function(){return row}};
var sent=[];global.document={getElementById:function(){return null}};
global.alert=function(m){sent.push('alert:'+m)};
async function post(p,b){sent.push([p,b]);return {ok:true}}
""" + fns + r"""
(async function(){toggleClinicalItem(box);await new Promise(function(r){setTimeout(r,20)});
process.stdout.write(JSON.stringify({sent:sent,ticks:remedies.map(function(r){return r.checked}),
 selected:classes.selected}))})();
"""
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    got = json.loads(out.stdout)
    assert got["ticks"] == [False, False]
    assert got["selected"] is False
    assert got["sent"] == [["/author/a1/clinical-items/selection",
                            {"label": "Fatigue", "remedies": []}]]
