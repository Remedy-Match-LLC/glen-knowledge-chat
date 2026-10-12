"""Nothing in the console inbox may mark a Gmail thread read.

Unread IS the reply queue. `openThread()` used to fire a background
mark-read the moment a thread was opened, before anyone had read a word
or decided a reply was owed. `backlog_summary()` counts unread Primary
mail as `awaiting_reply` and calls it the retention-risk queue, so
opening a client's email to triage it removed them from the count. The
metric improved because the evidence was gone. Measured 2026-09-07:
24 people waiting on a reply, 15 of them over five days, most marked read.

Two guards, tested here:
  1. The server refuses `read: true`. This one always wins, whoever calls.
  2. `openThread()` sends no mark-read request and does not clear the
     local unread flag.

Marking a thread UNREAD stays allowed. That only ever adds to the queue.
"""

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import app, dashboard
    from dashboard import inbox as _inbox
except Exception as e:  # pragma: no cover
    pytest.skip(f"app import needs secrets: {e}", allow_module_level=True)


def _repo():
    return Path(__file__).resolve().parent.parent


def _auth(mp):
    for obj in (app, dashboard):
        mp.setattr(obj, "CONSOLE_SECRET", "sek", raising=False)


def _post_read(value):
    return app.app.test_client().post(
        "/api/inbox/threads/T123/read",
        headers={"X-Console-Key": "sek", "Content-Type": "application/json"},
        data=json.dumps({"read": value}),
    )


# ── Guard 1: the server refuses to mark anything read ────────────────────────

def test_read_route_refuses_to_mark_read(monkeypatch):
    _auth(monkeypatch)
    calls = []
    monkeypatch.setattr(_inbox, "mark_read", lambda tid: calls.append(tid))
    monkeypatch.setattr(_inbox, "mark_unread", lambda tid: calls.append(tid))

    r = _post_read(True)
    body = r.get_json()

    assert calls == [], f"mark_read reached Gmail with {calls}"
    assert body.get("ok") is False, f"request succeeded: {body}"


def test_read_route_still_marks_unread(monkeypatch):
    """Marking unread only ever puts mail back in the queue, so it stays."""
    _auth(monkeypatch)
    unread = []
    monkeypatch.setattr(_inbox, "mark_read", lambda tid: pytest.fail("mark_read called"))
    monkeypatch.setattr(_inbox, "mark_unread", lambda tid: unread.append(tid))

    r = _post_read(False)
    body = r.get_json()

    assert unread == ["T123"], f"mark_unread not called, got {unread}"
    assert body.get("ok") is True, f"mark unread was refused: {body}"


# ── Guard 2: the page sends no mark-read on open ─────────────────────────────

def _extract_open_thread():
    html = (_repo() / "static" / "console-inbox.html").read_text()
    m = re.search(
        r"/\* === openThread \(test-extracted\) === \*/(.*?)"
        r"/\* === end openThread === \*/",
        html, re.S)
    assert m, "openThread marker block not found in console-inbox.html"
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_open_thread_sends_no_mark_read():
    script = textwrap.dedent("""
      const CALLS = [];
      let CURRENT_THREAD_ID = null, CURRENT_THREAD = null;
      const ALL_THREADS = [{ id: "T1", unread: true }];
      const el = () => ({ style: {}, classList: { add(){}, remove(){} },
                          innerHTML: "", textContent: "", value: "" });
      const document = { querySelectorAll: () => [], getElementById: el,
                         querySelector: () => null };
      const window = { innerWidth: 1400 };
      async function api(path, opts) { CALLS.push({ path, opts }); return { messages: [] }; }
      function renderThreadHeader() {}
      function renderMessage() {}
      function runAI() {}
      function toast() {}
      function assert(c, m){ if(!c){ console.error("FAIL: " + m); process.exit(1); } }
    """) + _extract_open_thread() + textwrap.dedent("""
      openThread("T1").then(() => {
        const read = CALLS.filter(c => /\\/read\\b/.test(c.path));
        assert(read.length === 0,
          "opening a thread sent a mark-read: " + JSON.stringify(read));
        assert(ALL_THREADS[0].unread === true,
          "opening a thread cleared the local unread flag");
        console.log("OK");
      });
    """)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Guard 3: no code removes the UNREAD label ────────────────────────────────

def _unread_removals(paths, root):
    """Every dict in the given source that puts UNREAD under removeLabelIds. Parsed, so a
    comment or docstring mentioning it does not count."""
    import ast
    hits = []
    for p in paths:
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "removeLabelIds"
                        and any(isinstance(c, ast.Constant) and c.value == "UNREAD"
                                for c in ast.walk(v))):
                    hits.append(f"{p.relative_to(root)}:{node.lineno}")
    return hits


def test_no_source_removes_the_unread_label():
    # Glen, 2026-10-11: "stop marking read". The Zelle import removed UNREAD from the bank
    # notifications it applied, from 2026-08-29.
    skip = {"tests", ".venv", "venv", "node_modules", ".git"}
    paths = [p for p in _repo().rglob("*.py") if not skip & set(p.relative_to(_repo()).parts)]
    assert len(paths) > 100
    assert _unread_removals(paths, _repo()) == []


def test_the_unread_scan_catches_a_removal(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text('body = {"addLabelIds": ["X"], "removeLabelIds": ["UNREAD"]}\n')
    assert _unread_removals([probe], tmp_path) == ["probe.py:1"]
    ok = tmp_path / "ok.py"
    ok.write_text('# removeLabelIds UNREAD in a comment does not count\nbody = {"removeLabelIds": ["INBOX"]}\n')
    assert _unread_removals([ok], tmp_path) == []
