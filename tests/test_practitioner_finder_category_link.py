"""Glen, 2026-10-06: a public finder link opens with Dental > Biological pressed,
and the visitor types a place. ?category=dental&sub=biological preselects; it never
searches without a location.

Runs the page's own applyCategoryPrefill in node against a stand-in DOM built from the
chips actually on the page.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "static" / "practitioner-finder.html").read_text()


def _fn():
    start = SRC.index("    function applyCategoryPrefill() {")
    return SRC[start:SRC.index("\n    }\n", start) + 6]


def _run(query):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    parents = re.findall(r'class="chip" data-parent="([^"]+)"', SRC)
    drills = {}
    for drill_id, body in re.findall(r'<div class="drill-down" id="drill-([^"]+)">(.*?)\n  </div>', SRC, re.S):
        drills[drill_id] = re.findall(r'data-sub="([^"]+)"', body)
    js = """
      const parents = %s, drills = %s, query = %s;
      function el(data) {
        return {dataset: data, attrs: {}, classes: new Set(),
                setAttribute(k, v) { this.attrs[k] = v; },
                classList: {add(c) { this._o.classes.add(c); }}};
      }
      const chips = parents.map(p => { const e = el({parent: p}); e.classList._o = e; return e; });
      const drillEls = {};
      for (const [id, subs] of Object.entries(drills)) {
        const d = el({}); d.classList._o = d;
        d.subs = subs.map(s => { const e = el({sub: s}); e.classList._o = e; return e; });
        d.querySelectorAll = () => d.subs;
        drillEls['drill-' + id] = d;
      }
      const document = {
        querySelectorAll: sel => sel === '.chip[data-parent]' ? chips : [],
        getElementById: id => drillEls[id] || null,
      };
      const window = {location: {search: query}};
      const filterState = {parent: null, sub: '__all__'};
      let searched = 0; function runSearch() { searched++; }
      %s
      applyCategoryPrefill();
      const pressed = chips.filter(c => c.attrs['aria-pressed'] === 'true').map(c => c.dataset.parent);
      const open = Object.entries(drillEls).filter(([k, d]) => d.classes.has('open')).map(([k]) => k);
      const subPressed = Object.values(drillEls).flatMap(d => d.subs)
        .filter(s => s.attrs['aria-pressed'] === 'true').map(s => s.dataset.sub);
      console.log(JSON.stringify({state: filterState, pressed, open, subPressed, searched}));
    """ % (json.dumps(parents), json.dumps(drills), json.dumps(query), _fn())
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_dental_biological_link_presses_both_chips_and_does_not_search():
    out = _run("?category=dental&sub=biological")
    assert out["state"] == {"parent": "dental", "sub": "biological"}
    assert out["pressed"] == ["dental"]
    assert out["open"] == ["drill-dental"]
    assert out["subPressed"] == ["biological"]
    assert out["searched"] == 0


def test_category_alone_opens_its_drill_down_at_all():
    out = _run("?category=dental")
    assert out["state"] == {"parent": "dental", "sub": "__all__"}
    assert out["subPressed"] == []


def test_unknown_values_change_nothing():
    assert _run("?category=dental'\"]&sub=biological")["state"] == {"parent": None, "sub": "__all__"}
    out = _run("?category=dental&sub=nope")
    assert out["state"] == {"parent": "dental", "sub": "__all__"}
    assert out["subPressed"] == []


def test_no_params_is_a_no_op():
    out = _run("")
    assert out["state"] == {"parent": None, "sub": "__all__"} and out["pressed"] == []


def test_prefill_runs_on_load_and_location_prefill_still_searches():
    assert "\n    applyCategoryPrefill();\n" in SRC
    start = SRC.index("function applyUrlPrefill() {")
    body = SRC[start:SRC.index("\n    }\n", start)]
    assert "if (!loc) return;" in body and "runSearch();" in body
