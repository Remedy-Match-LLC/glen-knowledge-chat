"""The browser referral capture must not touch rm_ref on a story-page link.

A ?story= link is credited only by the server hook (the story's own approved
affiliate, first touch kept). Both copies of the browser capture are executed in
node with a stubbed location, cookie jar and localStorage: static/ref-capture.js,
and the inline copy in static/index.html. Same node pattern as
tests/test_client_time_zone.py.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HARNESS = r"""
const vm = require('vm');
const src = %(src)s;
const cases = %(cases)s;
const out = [];
for (const search of cases) {
  const writes = [];
  const store = {};
  const ctx = {
    URLSearchParams,
    location: { search },
    localStorage: { setItem(k, v) { store[k] = v; }, getItem(k) { return store[k] || null; } },
    document: {},
  };
  Object.defineProperty(ctx.document, 'cookie', {
    get() { return 'rm_ref=first-touch'; },
    set(v) { writes.push(v); },
  });
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  out.push({ search, writes, stored: store.rm_ref || null });
}
console.log(JSON.stringify(out));
"""


def _run(src, cases, tmp_path):
    js = tmp_path / "harness.js"
    js.write_text(HARNESS % {"src": json.dumps(src), "cases": json.dumps(cases)})
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return {c["search"]: c for c in json.loads(r.stdout)}


def _index_inline_capture():
    html = (REPO / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"\(function captureRef\(\) \{.*?\}\)\(\);", html, re.S)
    assert m, "inline captureRef not found in static/index.html"
    return m.group(0)


STORY_CASES = ["?ref=other-aff&story=jane-doe", "?aff=other-aff&story=jane-doe",
               "?a=other-aff&story=jane-doe", "?story=jane-doe&ref=jane",
               "?utm_source=other-aff&utm_medium=referral&story=jane-doe"]


def test_shared_capture_ignores_story_links(tmp_path):
    src = (REPO / "static" / "ref-capture.js").read_text(encoding="utf-8")
    res = _run(src, STORY_CASES + ["?ref=other-aff", "?aff=other-aff"], tmp_path)
    for case in STORY_CASES:
        assert res[case]["writes"] == [] and res[case]["stored"] is None, case
    # The harness really runs the capture: without ?story= it writes as before.
    assert res["?ref=other-aff"]["writes"][0].startswith("rm_ref=other-aff")
    assert res["?aff=other-aff"]["stored"] == "other-aff"


def test_index_inline_capture_ignores_story_links(tmp_path):
    src = _index_inline_capture()
    res = _run(src, STORY_CASES + ["?ref=other-aff"], tmp_path)
    for case in STORY_CASES:
        assert res[case]["writes"] == [] and res[case]["stored"] is None, case
    assert res["?ref=other-aff"]["writes"][0].startswith("rm_ref=other-aff")


def test_no_other_browser_copy_writes_rm_ref():
    """Every place that writes the rm_ref cookie in the browser is one of the two
    copies covered above."""
    writers = []
    for f in (REPO / "static").rglob("*"):
        if f.suffix not in (".js", ".html"):
            continue
        if re.search(r"document\.cookie\s*=\s*['\"`]rm_ref=", f.read_text(encoding="utf-8",
                                                                           errors="ignore")):
            writers.append(f.relative_to(REPO).as_posix())
    assert sorted(writers) == ["static/index.html", "static/ref-capture.js"]
