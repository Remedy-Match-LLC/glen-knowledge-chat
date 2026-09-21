"""The Mine profile / Mine recent comms buttons must report their result beside them.

Glen, 2026-09-21, on Peach Goddard's intake: "When I click 'Mine profile -> stresses'
and 'Mine recent comms -> stresses' I see no effect. If it is indeed checking, it
should at least show some action in the UI."

Both calls had succeeded and found nothing new. The handlers wrote their result to
#rstat, which sits in the Live session (voice) block beside Record and Stop, a
different section of the page from the two buttons. So the answer was written where
he was not looking.

A server error was worse. post() calls r.json() unguarded, so a 500 with an HTML body
threw, the handler aborted, and the status stuck at "Mining..." forever. That is how
Balance All read as a hang for two days on 2026-09-18.

These tests RUN the two handlers in node against a stubbed page. A string match on
generated JavaScript proves nothing: it passes on a script the browser discards.
"""
import json
import re
import shutil
import subprocess
import tempfile

import pytest

from dashboard.biofield_report_html import render_author_html

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")

REP = {"test_id": "a41", "client": {"name": "Peach Goddard", "email": "p@example.com"},
       "date": "2026-09-21", "layers": [], "schedule": {"slots": [], "entries": []}}


def _html():
    return render_author_html(REP)


def _func(html, name):
    """One top-level function from the page script. This file writes each function
    at column 0 with its continuation lines indented, so a function ends at the next
    line that starts at column 0."""
    m = re.search(rf"(?ms)^(?:async )?function {name}\(.*?(?=^\S)", html)
    assert m, f"{name} not found in the page script"
    return m.group(0)


def _run(html, name, reply, *, throw=False):
    """Call handler `name` with post() stubbed to return `reply` (or reject), and
    return what each status element ended up showing."""
    stubs = """
const els = {};
const document = { getElementById(id) {
  if (!els[id]) els[id] = { id, textContent: '' };
  return els[id]; } };
function rstat(t){ document.getElementById('rstat').textContent = t; }
let loaded = 0;
function loadStress(){ loaded++; }
"""
    post = ("async function post(){ throw new SyntaxError("
            "'Unexpected token < in JSON at position 0'); }" if throw else
            f"async function post(){{ return {json.dumps(reply)}; }}")
    helpers = "".join(_func(html, h) for h in ("mstat",) if f"function {h}(" in html)
    body = (stubs + post + "\n" + helpers + _func(html, name) +
            f"\n{name}().then(() => console.log(JSON.stringify({{"
            "minestat: (els.minestat||{}).textContent || null,"
            "rstat: (els.rstat||{}).textContent || null, loaded })))"
            ".catch(e => console.log(JSON.stringify({uncaught: String(e)})));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(body)
        path = f.name
    r = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:800]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_status_line_sits_beside_the_two_mine_buttons():
    h = _html()
    comms = h.index("onclick=mineComms()")
    row_end = h.index("</div>", comms)
    assert "id=minestat" in h[comms:row_end], (
        "the Mine buttons' status must be in the same button row, not elsewhere")


@pytest.mark.parametrize("name,added,expected", [
    ("mineProfile", 0, "No new stresses found in the profile."),
    ("mineProfile", 3, "Added 3 stress(es) from the profile."),
    ("mineComms", 0, "No new stresses found in recent comms."),
    ("mineComms", 2, "Added 2 stress(es) from recent comms."),
])
def test_result_shows_beside_the_button(name, added, expected):
    out = _run(_html(), name, {"added": added})
    assert out.get("minestat") == expected
    assert out["loaded"] == 1, "the stress list must still refresh"


@pytest.mark.parametrize("name,label", [("mineProfile", "Mine profile"),
                                        ("mineComms", "Mine comms")])
def test_a_route_error_is_shown_not_swallowed(name, label):
    out = _run(_html(), name, {"error": "No client selected yet"})
    assert out.get("minestat") == f"{label}: No client selected yet"


@pytest.mark.parametrize("name,label", [("mineProfile", "Mine profile"),
                                        ("mineComms", "Mine comms")])
def test_a_server_failure_ends_the_waiting_message(name, label):
    """A 500 with an HTML body makes r.json() throw. The button must say it failed
    rather than sit on 'Mining...' forever."""
    out = _run(_html(), name, None, throw=True)
    assert "uncaught" not in out, f"the handler let the error escape: {out}"
    status = out.get("minestat") or ""
    assert status.startswith(f"{label} failed"), status
    assert "Mining" not in status
