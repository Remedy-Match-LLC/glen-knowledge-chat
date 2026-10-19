"""The Clinical summary control for Glen's combine. The row you are on survives and
the one you pick is absorbed, so the label on the button has to say which way round
it goes."""
import re

from dashboard.biofield_report_html import render_author_html, render_clinical_checklist

def _row(label):
    return {"label": label, "checked": False, "covered_by": "", "layer": None,
            "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
            "pattern_is_suggested": False}


ROWS = [_row("Cataracts"), _row("Cataract"), _row("Dry eye")]


def test_each_row_offers_the_other_rows_to_absorb():
    h = render_clinical_checklist(ROWS, [])
    block = h[h.index('data-label="Cataracts"'):h.index('data-label="Cataract"')]
    opts = re.findall(r"<option value=\"([^\"]+)\">", block)
    assert "Cataract" in opts and "Dry eye" in opts
    assert "Cataracts" not in opts          # a condition cannot absorb itself


def test_the_button_says_which_way_round_it_goes():
    h = render_clinical_checklist(ROWS, [])
    assert "Combine into this" in h


def test_a_lone_row_offers_no_combine():
    h = render_clinical_checklist([_row("Cataracts")], [])
    assert "Combine into this" not in h


def test_the_handler_is_on_the_page():
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "", clinical_checklist=ROWS)
    assert "function combineClinicalItems" in h
    assert "/author/a7/clinical-items/combine" in h


# Glen, 2026-10-18: "Clinical issues need another option: Combine this into another row."

def test_each_row_also_offers_to_fold_itself_into_another():
    h = render_clinical_checklist(ROWS, [])
    block = h[h.index('data-label="Cataracts"'):h.index('data-label="Cataract"')]
    into = block[block.index("clinical-combine-into"):]
    opts = re.findall(r"<option value=\"([^\"]+)\">", into[:into.index("</select>")])
    assert opts == ["Cataract", "Dry eye"]
    assert "Combine this into another row" in into and "combineClinicalItems(this,true)" in into


def test_a_lone_row_offers_neither_direction():
    assert "Combine this into another row" not in render_clinical_checklist([_row("Cataracts")], [])


def _combine(into):
    import json
    import shutil
    import subprocess
    import pytest
    from dashboard import biofield_report_html as rh
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = rh._AUTHOR_JS
    start = src.index("async function combineClinicalItems(")
    fn = src[start:src.index("\nasync function", start + 10)]
    js = """
var posted=[],asked=[];var location={reload:function(){}};
function confirm(t){asked.push(t);return true}
function prompt(t,d){asked.push(d);return null}
function alert(t){asked.push(t)}
async function post(url,body){posted.push(body);return {ok:true}}
var pick={value:'Dry eye'},row={dataset:{label:'Cataracts'}};
var btn={closest:function(){return row},parentNode:{querySelector:function(){return pick}}};
FN
combineClinicalItems(btn,INTO).then(function(){console.log(JSON.stringify({posted:posted,asked:asked}))});
""".replace("FN", fn).replace("INTO", json.dumps(into))
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_combine_into_this_keeps_this_row():
    got = _combine(False)
    assert got["posted"] == [{"absorbed": "Dry eye", "survivor": "Cataracts", "display": ""}]
    assert got["asked"][0].startswith('Combine "Dry eye" into "Cataracts"?')


def test_combine_into_that_keeps_the_picked_row():
    got = _combine(True)
    assert got["posted"] == [{"absorbed": "Cataracts", "survivor": "Dry eye", "display": ""}]
    assert got["asked"][0].startswith('Combine "Cataracts" into "Dry eye"?')
    assert "every client" in got["asked"][0] and got["asked"][1] == "Dry eye + Cataracts"
