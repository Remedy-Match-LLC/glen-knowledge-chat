"""Diet guidance by terrain phase (Glen approved the text 2026-10-16; rnd-1b build request).

Each client sees the intro (every caution line) and only their own phase's section, on the
intake form once the terrain question is answered. Glen took it out of the Biofield report
and the portal report on 2026-10-21, on screen and in print; the intake forms keep it.
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
# Changed 2026-10-20: three lines no longer defer to a doctor (Glen, "approve", clinical tab;
# the fasting line from report-leads-2026-10-20/diet-line-APPROVED.txt, the others from
# vault clinical/biofield-sections/explanations/review/batch-12-report-leads/final.json).
APPROVED_SHA256 = "9d30036e9d6322bfe9b0f727cf8759871517b15ec179d43ecfd9dc8ce1f9f8be"
CAUTION = ("If you have diabetes or are pregnant or breastfeeding, ask Dr. Glen in your "
           "portal chat before fasting or dieting. The same applies if you take any "
           "medicine or have ever had an eating disorder.")


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
        mine = "\n".join(dg.guidance(n)["paragraphs"])
        for m in range(1, 6):
            if m == n:
                continue
            assert dg.guidance(m)["paragraphs"][0] not in mine


def test_every_source_line_is_its_own_paragraph():
    g = dg.guidance(4)
    assert "An overnight fast of 12 to 14 hours suits those with strength to spare." in g["paragraphs"]
    assert len(g["intro"]) == 5 and len(g["paragraphs"]) == 6
    assert any(p.startswith("Better out than in: help your body's own elimination")
               for p in g["paragraphs"])


@pytest.mark.parametrize("bad", [None, "", 0, 6, "phase 3", "x"])
def test_no_valid_phase_shows_nothing(bad):
    assert dg.guidance(bad) is None


def test_the_intake_phase_is_the_lowest_ticked():
    assert dg.intake_phase({"terrain": 2, "terrain_selections": [4, 2, 5]}) == 2
    assert dg.intake_phase({"terrain_selections": ["5", "3"]}) == 3
    assert dg.intake_phase({"terrain": 4}) == 4


def test_an_edited_terrain_beats_selections_left_behind():
    """intake_public.merge_answers keeps old selections when terrain is edited (round 1)."""
    from dashboard import intake_public
    merged = intake_public.merge_answers({"terrain": 2, "terrain_selections": [2, 5]},
                                         {"terrain": 4, "terrain_selections": [4]})
    assert merged["terrain"] == 4
    assert dg.intake_phase(merged) == 4
    assert dg.intake_phase({"_external": True}) is None
    assert dg.intake_phase(None) is None


@pytest.mark.parametrize("row,want", [
    ({"status": "submitted", "answers": {"terrain": 3}}, {"terrain": 3}),
    ({"status": "draft", "answers": {"terrain": 3}}, None),
    ({"status": "submitted", "answers": {"terrain": 3, "_external": True}}, None),
    ({"status": "submitted", "answers": None}, None),
    (None, None),
])
def test_only_a_submitted_intake_can_be_the_report_fallback(row, want):
    assert dg.report_fallback_answers(row) == want


def test_the_report_only_helpers_are_gone():
    """Nothing renders the guidance as report HTML any more (Glen, 2026-10-21)."""
    for name in ("guidance_html", "report_phase", "_linked"):
        assert not hasattr(dg, name), name


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


# --- the PDF report (biofield_report_present, made on Glen's Mac): no longer shown ---

def _report(**kw):
    return {"client": {"name": "Test", "email": "t@example.com"}, "date": "2026-10-16",
            "layers": [], "schedule": {}, **kw}


def _absent(html):
    assert "Eating for your terrain" not in html
    assert "diet-guidance" not in html
    assert CAUTION not in html
    for n in range(1, 6):
        assert dg.guidance(n)["paragraphs"][0] not in html, n


def test_the_pdf_no_longer_shows_the_tested_phase_guidance():
    from dashboard.biofield_report_present import render_present
    html = render_present(_report(phase=4, intake_answers={"terrain": 1}))
    assert "Remedy Schedule" in html
    _absent(html)


def test_the_pdf_no_longer_falls_back_to_the_intake():
    from dashboard.biofield_report_present import render_present
    _absent(render_present(_report(intake_answers={"terrain_selections": [3, 2]})))
    _absent(render_present(_report()))


def test_the_local_app_no_longer_adds_an_intake_fallback():
    """The fallback fed only the diet section. The Kloud program fetches the intake itself
    when it needs it (biofield_local_app._kloud_program)."""
    import biofield_local_app as bla
    assert not hasattr(bla, "_add_intake_fallback")
    src = (ROOT / "biofield_local_app.py").read_text()
    assert "_add_intake_fallback" not in src


def test_the_local_fetch_sends_the_key_in_a_header_only(monkeypatch):
    import urllib.request
    import biofield_local_app as bla
    seen = {}

    class _R:
        def read(self):
            return b'{"status": "submitted", "answers": {"terrain": 2}}'

    class _Opener:
        def open(self, req, timeout=0):
            seen["url"], seen["key"] = req.full_url, req.get_header("X-console-key")
            return _R()

    def _build(*handlers):
        seen["handlers"] = handlers
        return _Opener()
    monkeypatch.setenv("CONSOLE_SECRET", "ci-secret")
    monkeypatch.setattr(urllib.request, "build_opener", _build)
    assert bla._fetch_intake_answers("T@Example.com") == {"terrain": 2}
    assert seen["key"] == "ci-secret" and "ci-secret" not in seen["url"]
    assert seen["url"].endswith("/api/console/intake/t%40example.com")
    # The opener refuses redirects, so the key header never follows one elsewhere.
    (handler,) = seen["handlers"]
    assert handler().redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_the_local_fetch_never_follows_a_redirect_for_real(monkeypatch):
    import http.server
    import threading
    import biofield_local_app as bla
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("X-Console-Key")))
            if self.path.startswith("/api/console/intake/"):
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"answers": {"terrain": 1}}')

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CONSOLE_SECRET", "ci-secret")
        monkeypatch.setenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
        assert bla._fetch_intake_answers("a@example.com") is None
        assert [p for p, _ in hits] == ["/api/console/intake/a%40example.com"]
    finally:
        srv.shutdown()


def test_a_missing_file_shows_nothing_and_breaks_nothing(monkeypatch, tmp_path):
    from dashboard.biofield_report_present import render_present
    monkeypatch.setattr(dg, "_PATH", str(tmp_path / "nope.md"))
    monkeypatch.setattr(dg, "_LOADED", None)
    assert dg.guidance(3) is None
    out = dg.form_with_guidance(intake.INTAKE_FORM)
    assert all("diet_guidance" not in f for s in out["sections"] for f in s["fields"])
    assert "Remedy Schedule" in render_present(_report(phase=3))


def test_a_malformed_file_shows_nothing(monkeypatch, tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("## Something else\n\nHello.\n")
    monkeypatch.setattr(dg, "_PATH", str(bad))
    monkeypatch.setattr(dg, "_LOADED", None)
    assert dg.guidance(1) is None


# --- the portal report payload ---

@pytest.fixture()
def portal(tmp_db, monkeypatch):
    import app
    from dashboard import client_portal as cp
    monkeypatch.setattr(app, "LOG_DB", tmp_db)

    def make(email, content, answers=None, status="submitted"):
        with sqlite3.connect(tmp_db) as cx:
            cx.row_factory = sqlite3.Row
            cp.init_client_portal_table(cx)
            token, _ = cp.upsert_portal(cx, email, "Client", content)
            if answers is not None:
                intake.init_intake_table(cx)
                (intake.save_draft if status == "draft" else intake.submit)(cx, email, answers, "t")
        return app.app.test_client().get(f"/api/portal/{token}").get_json()
    return make


def test_the_portal_payload_has_no_diet_guidance_with_a_tested_phase(portal):
    j = portal("p1@example.com", {"phase": 4}, {"terrain": 1})
    assert "diet_guidance" not in j and j["phase"] == 4


def test_the_portal_payload_has_no_diet_guidance_from_the_intake(portal):
    j = portal("p2@example.com", {}, {"terrain": 2, "terrain_selections": [2, 5]})
    assert "diet_guidance" not in j


def test_the_portal_payload_never_looks_the_guidance_up(portal, monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "guidance", lambda n: calls.append(n))
    j = portal("p4@example.com", {"phase": 3}, {"terrain": 3})
    assert calls == [] and "diet_guidance" not in j and "phase" in j


# --- the intake form routes ---

def test_both_form_routes_attach_the_guidance_to_terrain():
    import inspect
    import app
    for fn in (app.intake_form, app.intake_public_form):
        assert "form_with_guidance(_intake.INTAKE_FORM)" in inspect.getsource(fn)


# --- the pages ---

PORTAL = (ROOT / "static" / "client-portal.html").read_text()
PUBLIC = (ROOT / "static" / "begin-intake.html").read_text()


def test_the_portal_report_and_its_print_view_no_longer_render_it():
    assert "dietGuidanceHtml" not in PORTAL
    assert "d.diet_guidance" not in PORTAL
    css = PORTAL[:PORTAL.index("</style>")]
    assert ".diet-guidance" not in css.replace(".intake-diet-guidance", "")
    # The intake form's own box stays.
    assert ".intake-diet-guidance{" in css and "f.diet_guidance" in PORTAL


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
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_portal_intake_shows_only_the_scored_phase(tmp_path):
    form = dg.form_with_guidance(intake.INTAKE_FORM)
    field = [f for s in form["sections"] for f in s["fields"] if f["id"] == "terrain"][0]
    js = tmp_path / "h.js"
    js.write_text(HARNESS.replace("RENDERER", _fn(PORTAL, "renderIntakeField"))
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


def test_the_local_fetch_ignores_a_draft_intake(monkeypatch):
    import urllib.request
    import biofield_local_app as bla

    class _R:
        def read(self):
            return b'{"status": "draft", "answers": {"terrain": 2}}'

    class _Opener:
        def open(self, req, timeout=0):
            return _R()
    monkeypatch.setenv("CONSOLE_SECRET", "ci-secret")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: _Opener())
    assert bla._fetch_intake_answers("a@example.com") is None
