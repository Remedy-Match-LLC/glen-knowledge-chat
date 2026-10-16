"""Diet guidance by terrain phase (Glen approved the text 2026-10-16; rnd-1b build request).

Each client sees the intro (every caution line) and only their own phase's section, in the
Biofield report (tested phase first, the intake answer as fallback: Glen, 2026-10-16) and on
the intake form once the terrain question is answered.
"""
import hashlib
import json
import pathlib
import shutil
import sqlite3
import subprocess

import pytest

from dashboard import diet_guidance as dg
from dashboard import intake
from dashboard.terrain_phase import PHASE_NAMES

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The client text from rnd/review/2026-10-16-client-diet-guidance/client-diet-guidance-draft-v1.md
# (source sha256 ecab1ee2fbe7ffde...), "## Eating for your terrain" to the end, byte for byte.
# A wording change goes back to Glen through rnd, and then this hash changes with it.
APPROVED_SHA256 = "ed7bc97e7cbeed632dcdb6c9b5b3a12e6bf4774446062cbd786a16e146c91925"
CAUTION = ("If you have diabetes, take regular medication, are pregnant, or have ever "
           "struggled with eating, ask your doctor before cutting back on any food or fasting.")


def test_the_text_is_the_approved_text_byte_for_byte():
    raw = (ROOT / "data" / "diet-guidance.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == APPROVED_SHA256


def test_each_phase_has_its_section_and_the_intro_cautions():
    for n, name in PHASE_NAMES.items():
        g = dg.guidance(n)
        assert g["heading"] == f"{name} (Phase {n})"
        assert g["title"] == "Eating for your terrain"
        assert CAUTION in g["intro"] and len(g["intro"]) == 5
        assert g["paragraphs"] and all(p.strip() == p for p in g["paragraphs"])


def test_one_phase_never_carries_another_phase_s_text():
    for n in range(1, 6):
        html = dg.guidance_html(n)
        for m in range(1, 6):
            if m == n:
                continue
            assert dg.guidance(m)["heading"] not in html
            assert dg.guidance(m)["paragraphs"][0] not in html


def test_every_sentence_is_its_own_paragraph():
    html = dg.guidance_html(4)
    assert "<p>An overnight fast of 12 to 14 hours suits those with strength to spare.</p>" in html
    assert html.count("<p>") == 5 + 6
    assert "Better out than in: help your body&#x27;s own elimination" in html


@pytest.mark.parametrize("bad", [None, "", 0, 6, "phase 3", "x"])
def test_no_valid_phase_shows_nothing(bad):
    assert dg.guidance(bad) is None and dg.guidance_html(bad) == ""


def test_the_intake_phase_is_the_lowest_ticked():
    assert dg.intake_phase({"terrain": 2, "terrain_selections": [4, 2, 5]}) == 2
    assert dg.intake_phase({"terrain_selections": ["5", "3"]}) == 3
    assert dg.intake_phase({"terrain": 4}) == 4
    assert dg.intake_phase({"_external": True}) is None
    assert dg.intake_phase(None) is None


def test_the_report_uses_the_tested_phase_and_falls_back_to_the_intake():
    assert dg.report_phase(5, {"terrain": 1}) == 5
    assert dg.report_phase("3", None) == 3
    assert dg.report_phase(None, {"terrain_selections": [4, 2]}) == 2
    assert dg.report_phase("", {}) is None


def test_the_parser_refuses_a_broken_copy():
    text = (ROOT / "data" / "diet-guidance.md").read_text()
    with pytest.raises(ValueError):
        dg._parse(text.replace("### Terrain Renew (Phase 3)", "### Terrain Renewal (Phase 3)"))
    with pytest.raises(ValueError):
        dg._parse(text.replace("## Eating for your terrain", "## Eating well"))


def test_the_form_copy_carries_all_five_and_the_form_is_untouched():
    out = dg.form_with_guidance(intake.INTAKE_FORM)
    terrain = [f for s in out["sections"] for f in s["fields"] if f["id"] == "terrain"][0]
    assert sorted(terrain["diet_guidance"]) == ["1", "2", "3", "4", "5"]
    assert terrain["diet_guidance"]["3"] == dg.guidance(3)
    orig = [f for s in intake.INTAKE_FORM["sections"] for f in s["fields"] if f["id"] == "terrain"]
    assert "diet_guidance" not in orig[0]
    others = [f for s in out["sections"] for f in s["fields"] if f["id"] != "terrain"]
    assert all("diet_guidance" not in f for f in others)


# --- the PDF report (biofield_report_present, made on Glen's Mac) ---

def _report(**kw):
    return {"client": {"name": "Test", "email": "t@example.com"}, "date": "2026-10-16",
            "layers": [], "schedule": {}, **kw}


def test_the_pdf_shows_the_tested_phase_after_the_remedy_content():
    from dashboard.biofield_report_present import render_present
    html = render_present(_report(phase=4, intake_answers={"terrain": 1}))
    assert "Terrain Refresh (Phase 4)" in html and "Terrain Revive (Phase 1)" not in html
    assert html.index("Remedy Schedule") < html.index("Eating for your terrain") \
        < html.index('class="footer"')


def test_the_pdf_falls_back_to_the_intake_and_shows_nothing_without_either():
    from dashboard.biofield_report_present import render_present
    assert "Terrain Repair (Phase 2)" in render_present(
        _report(intake_answers={"terrain_selections": [3, 2]}))
    assert "Eating for your terrain" not in render_present(_report())


def test_the_local_app_fetches_the_intake_only_when_there_is_no_tested_phase(monkeypatch):
    import biofield_local_app as bla
    calls = []
    monkeypatch.setattr(bla, "_fetch_intake_answers",
                        lambda email: calls.append(email) or {"terrain": 3})
    rep = bla._add_intake_fallback(_report(phase=5))
    assert calls == [] and "intake_answers" not in rep
    rep = bla._add_intake_fallback(_report())
    assert calls == ["t@example.com"] and rep["intake_answers"] == {"terrain": 3}


def test_every_local_render_adds_the_fallback():
    src = (ROOT / "biofield_local_app.py").read_text()
    assert src.count("        _add_intake_fallback(rep)\n") == 3


def test_the_local_fetch_sends_the_key_in_a_header_only(monkeypatch):
    import urllib.request
    import biofield_local_app as bla
    seen = {}

    class _R:
        def read(self):
            return b'{"answers": {"terrain": 2}}'

    def _open(req, timeout=0):
        seen["url"], seen["key"] = req.full_url, req.get_header("X-console-key")
        return _R()
    monkeypatch.setenv("CONSOLE_SECRET", "ci-secret")
    monkeypatch.setattr(urllib.request, "urlopen", _open)
    assert bla._fetch_intake_answers("T@Example.com") == {"terrain": 2}
    assert seen["key"] == "ci-secret" and "ci-secret" not in seen["url"]
    assert seen["url"].endswith("/api/console/intake/t%40example.com")


# --- the portal report payload ---

@pytest.fixture()
def portal(tmp_db, monkeypatch):
    import app
    from dashboard import client_portal as cp
    monkeypatch.setattr(app, "LOG_DB", tmp_db)

    def make(email, content, answers=None):
        with sqlite3.connect(tmp_db) as cx:
            cx.row_factory = sqlite3.Row
            cp.init_client_portal_table(cx)
            token, _ = cp.upsert_portal(cx, email, "Client", content)
            if answers is not None:
                intake.init_intake_table(cx)
                intake.save_draft(cx, email, answers, "t")
        return app.app.test_client().get(f"/api/portal/{token}").get_json()
    return make


def test_the_portal_payload_carries_the_tested_phase(portal):
    j = portal("p1@example.com", {"phase": 4}, {"terrain": 1})
    assert j["diet_guidance"]["heading"] == "Terrain Refresh (Phase 4)"


def test_the_portal_payload_falls_back_to_the_intake(portal):
    j = portal("p2@example.com", {}, {"terrain": 2, "terrain_selections": [2, 5]})
    assert j["diet_guidance"]["heading"] == "Terrain Repair (Phase 2)"


def test_the_portal_payload_has_none_without_either(portal):
    assert portal("p3@example.com", {})["diet_guidance"] is None


def test_a_guidance_error_does_not_break_the_payload(portal, monkeypatch):
    monkeypatch.setattr(dg, "guidance", lambda n: 1 / 0)
    j = portal("p4@example.com", {"phase": 3})
    assert j["diet_guidance"] is None and "phase" in j


# --- the intake form routes ---

def test_both_form_routes_attach_the_guidance_to_terrain():
    import inspect
    import app
    for fn in (app.intake_form, app.intake_public_form):
        assert "form_with_guidance(_intake.INTAKE_FORM)" in inspect.getsource(fn)


# --- the pages ---

PORTAL = (ROOT / "static" / "client-portal.html").read_text()
PUBLIC = (ROOT / "static" / "begin-intake.html").read_text()


def test_the_portal_report_and_its_print_view_render_it():
    assert '${dietGuidanceHtml(d.diet_guidance, "screen")}' in PORTAL
    assert '${dietGuidanceHtml(d.diet_guidance, "print")}' in PORTAL


def _fn(page, name):
    at = page.find(f"\nfunction {name}(")
    assert at != -1
    return page[at:page.find("\n}\n", at) + 3]


HARNESS = r"""
const assert = require('assert');
function el(tag){
  const e = {tagName: tag.toUpperCase(), children: [], listeners: {}, className: '', textContent: '',
    type: '', value: '', checked: false, hidden: false, dataset: {}, name: '',
    get firstChild(){ return this.children[0]; },
    removeChild(c){ this.children.splice(this.children.indexOf(c), 1); return c; },
    appendChild(c){ this.children.push(c); return c; },
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    querySelectorAll(sel){
      const out = []; (function walk(n){ n.children.forEach(c => { out.push(c); walk(c); }); })(this);
      if (sel === 'input:checked') return out.filter(n => n.tagName === 'INPUT' && n.checked);
      if (sel === 'input') return out.filter(n => n.tagName === 'INPUT');
      return [];
    },
  };
  return e;
}
global.document = {createElement: el};
const esc = (s)=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function all(n){ const out = []; (function walk(x){ x.children.forEach(c => { out.push(c); walk(c); }); })(n); return out; }
function text(n){ return all(n).map(c => c.textContent).join('\n'); }

RENDERER
DIETHTML

const field = FIELD;
const getters = {}, els = {};
const w = renderIntakeField(field, null, getters, els, () => {}, {}, null);
const dgBox = all(w).find(c => c.className === 'intake-diet-guidance');
assert.ok(dgBox, 'guidance box');
assert.strictEqual(dgBox.hidden, true);                       // nothing ticked: nothing shown
const boxes = all(w).filter(c => c.tagName === 'INPUT');
const tick = (v, on) => { const b = boxes.find(b => b.value === String(v)); b.checked = on;
  b.listeners.change.forEach(f => f()); };
tick(4, true);
assert.strictEqual(dgBox.hidden, false);
assert.ok(text(dgBox).includes('Terrain Refresh (Phase 4)'));
assert.ok(text(dgBox).includes(CAUTION));
tick(2, true);                                                // lowest ticked wins
assert.ok(text(dgBox).includes('Terrain Repair (Phase 2)'));
assert.ok(!text(dgBox).includes('Terrain Refresh'));
tick(2, false); tick(4, false);
assert.strictEqual(dgBox.hidden, true);
// a saved answer shows its guidance on load
const w2 = renderIntakeField(field, 3, {}, {}, () => {}, {}, [3, 5]);
assert.ok(text(all(w2).find(c => c.className === 'intake-diet-guidance')).includes('Terrain Renew (Phase 3)'));
// the report block: one phase, escaped, nothing for null
const h = dietGuidanceHtml(field.diet_guidance["5"], "screen");
assert.ok(h.includes('Terrain Relief (Phase 5)') && !h.includes('Terrain Revive'));
assert.strictEqual(dietGuidanceHtml(null, "screen"), "");
assert.ok(dietGuidanceHtml({title: "t", heading: "<b>", intro: [], paragraphs: ["a&b"]}, "print").includes("&lt;b&gt;"));
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_portal_intake_shows_only_the_scored_phase(tmp_path):
    form = dg.form_with_guidance(intake.INTAKE_FORM)
    field = [f for s in form["sections"] for f in s["fields"] if f["id"] == "terrain"][0]
    js = tmp_path / "h.js"
    js.write_text(HARNESS.replace("RENDERER", _fn(PORTAL, "renderIntakeField"))
                  .replace("DIETHTML", _fn(PORTAL, "dietGuidanceHtml"))
                  .replace("FIELD", json.dumps(field)).replace("CAUTION", json.dumps(CAUTION)))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout


def test_the_public_intake_shows_it_on_change_and_on_a_chat_fill():
    at = PUBLIC.find('if(f.type==="scale"){')
    block = PUBLIC[at:PUBLIC.find("S.fieldEls[f.id]=g; return wrap;", at)]
    assert "f.diet_guidance[String(S.getters[f.id]())]" in block
    assert 'rd.addEventListener("change",showGuidance)' in block
    assert "if(r)r.checked=true;showGuidance();" in block
    assert "innerHTML" not in block
