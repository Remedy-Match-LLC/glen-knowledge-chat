# tests/test_email_cta_needs_a_click.py
"""An "email" CTA on a chat answer is a button. It never sends on arrival.

2026-10-28: the embed sent the full report the moment an email CTA arrived, using the
address saved in the browser. One client got 9 reports in one conversation.
The renderer is run in node against a stub DOM, so the test drives the real function.
"""
import json
import re
import shutil
import subprocess

import pytest

SRC = open("static/embed.html").read()
NODE = shutil.which("node")


def _function(name):
    start = SRC.index(f"function {name}(")
    depth, i = 0, SRC.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(SRC[i], 0)
        if depth == 0:
            return SRC[start:i + 1]
        i += 1


HARNESS = r"""
let sends = 0, appended = [];
function handleGatedFullReport(id) { sends++; }
function mk(tag) {
  const el = { tag, style: {}, disabled: false, listeners: {}, textContent: '',
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { appended.push(c); } };
  return el;
}
const lastMsg = mk('div');
const msgs = { scrollTop: 0, scrollHeight: 1, querySelector: () => lastMsg };
const document = { createElement: mk, getElementById: () => msgs };
function fetch() {}
%s
renderCta(%s, 7);
const before = sends;
const b = appended[0];
if (b && b.listeners.click) { b.listeners.click(); b.listeners.click(); }
console.log(JSON.stringify({before, after: sends, tag: b && b.tag,
  label: b && b.textContent, n: appended.length}));
"""


def _run(cta):
    out = subprocess.run([NODE, "-e", HARNESS % (_function("renderCta"), json.dumps(cta))],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


needs_node = pytest.mark.skipif(not NODE, reason="node not installed")


@needs_node
def test_an_email_cta_sends_nothing_until_clicked():
    r = _run({"type": "email"})
    assert r["before"] == 0
    assert r["tag"] == "button"


@needs_node
def test_one_click_sends_once():
    r = _run({"type": "email"})
    assert r["after"] == 1


@needs_node
def test_the_button_keeps_the_models_label():
    assert _run({"type": "email", "label": "Send me this"})["label"] == "Send me this"
    assert _run({"type": "email"})["label"] == "Email me the full report"


@needs_node
def test_a_page_cta_still_renders_a_link():
    r = _run({"type": "page", "target": "/x", "label": "Go"})
    assert r["before"] == 0 and r["tag"] == "a" and r["n"] == 1


def test_nothing_else_in_the_embed_sends_on_an_email_cta():
    """The only automatic callers left are the server's own gate events."""
    calls = [m.start() for m in re.finditer(r"handleGatedFullReport\(", SRC)]
    contexts = [SRC[max(0, c - 400):c] for c in calls]
    auto = [c for c in contexts
            if not c.endswith("function ")
            and "evt.gated === 'email_required'" not in c
            and "addEventListener('click'" not in c]
    assert not auto, auto
