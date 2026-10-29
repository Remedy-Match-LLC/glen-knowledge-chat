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
    if SRC[start - 6:start] == "async ":
        start -= 6
    depth, i = 0, SRC.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(SRC[i], 0)
        if depth == 0:
            return SRC[start:i + 1]
        i += 1


HARNESS = r"""
let sends = 0, appended = [], ids = [];
const RESULT = %s, NOCLICK = %s;
function handleGatedFullReport(id) { sends++; ids.push(id); return Promise.resolve(RESULT); }
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
// The count at exit sees a send scheduled for any later time (round 2 review).
process.on('exit', () => console.log(JSON.stringify({total: sends})));
if (!NOCLICK) (async () => {
  // Let any timer or promise queued on arrival run before counting (round 3 review).
  await new Promise(r => setTimeout(r, 20));
  const before = sends;
  const b = appended[0];
  if (b && b.listeners.click) { b.listeners.click(); b.listeners.click(); }
  const after = sends;
  await new Promise(r => setTimeout(r, 0));
  if (b && b.listeners.click) b.listeners.click();
  console.log(JSON.stringify({before, after, retried: sends, ids, tag: b && b.tag,
    label: b && b.textContent, n: appended.length}));
})();
"""


def _run(cta, result=True, noclick=False):
    out = subprocess.run([NODE, "-e", HARNESS % (json.dumps(result), json.dumps(noclick),
                                                 _function("renderCta"), json.dumps(cta))],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    lines = [json.loads(x) for x in out.stdout.strip().splitlines()]
    return {**(lines[0] if noclick else lines[-2]), "total": lines[-1]["total"]}


needs_node = pytest.mark.skipif(not NODE, reason="node not installed")


@needs_node
def test_an_email_cta_sends_nothing_until_clicked():
    r = _run({"type": "email"})
    assert r["before"] == 0
    assert r["tag"] == "button"
    assert _run({"type": "email"}, noclick=True)["total"] == 0


@needs_node
def test_one_click_sends_once():
    r = _run({"type": "email"})
    assert r["before"] == 0 and r["after"] == 1
    assert r["ids"] == [7]          # the answer's own log id (round 3 review)


@needs_node
def test_a_sent_report_never_sends_again_from_that_button():
    assert _run({"type": "email"}, result=True)["retried"] == 1


@needs_node
@pytest.mark.parametrize("result", [False, None])
def test_a_failed_or_cancelled_send_can_be_retried(result):
    """Round 2 review: the button must not stay disabled when nothing was sent."""
    assert _run({"type": "email"}, result=result)["retried"] == 2


@needs_node
def test_the_button_keeps_the_models_label():
    assert _run({"type": "email", "label": "Send me this"})["label"] == "Send me this"
    assert _run({"type": "email"})["label"] == "Email me the full report"


@needs_node
def test_a_page_cta_still_renders_a_link():
    r = _run({"type": "page", "target": "/x", "label": "Go"})
    assert r["before"] == 0 and r["tag"] == "a" and r["n"] == 1


def test_nothing_else_in_the_embed_sends_on_an_email_cta():
    """Exactly two calls remain: the button's click, and the server's gate event.

    Round 2 review: a nearby-text check let an unconditional call slip through, so
    each call must sit on the line right after its own condition or listener."""
    lines = SRC.splitlines()
    calls = [i for i, l in enumerate(lines)
             if "handleGatedFullReport(" in l and "function handleGatedFullReport" not in l]
    assert len(calls) == 2, [lines[i] for i in calls]
    guards = []
    for i in calls:
        prev = [l.strip() for l in lines[max(0, i - 6):i]]
        guards.append(next((p for p in reversed(prev)
                            if p.startswith(("if (evt.gated === 'email_required')",
                                             "Promise.resolve(", "ebtn.addEventListener('click'"))),
                           None))
    assert all(guards), list(zip([lines[i] for i in calls], guards))
    # An alias would hide a call from the scan above (round 3 review).
    assert SRC.count("handleGatedFullReport") == 3


SEND = r"""
const els = { 'optin-email': { value: 'a@b.co' }, 'optin-name': { value: '' } };
const document = { getElementById: (id) => els[id] || null };
const localStorage = { getItem: () => '', setItem() {} };
function prompt() { return ''; }
function appendMessage() {}
let posted = 0;
async function fetch() { posted++; return { ok: %s, status: 500, json: async () => (%s) }; }
%s
handleGatedFullReport(7).then(r => console.log(JSON.stringify({r: r === true, posted})));
"""


def _send(ok, body):
    src = SEND % (json.dumps(ok), json.dumps(body), _function("handleGatedFullReport"))
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_send_reports_success_so_the_button_stays_spent():
    assert _send(True, {"ok": True}) == {"r": True, "posted": 1}


@needs_node
def test_the_send_reports_failure_so_the_button_can_retry():
    assert _send(False, {"error": "x"})["r"] is False
    assert _send(True, {"ok": False})["r"] is False
