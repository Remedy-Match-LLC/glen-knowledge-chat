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

# Every "UNREAD" literal in the app's Python, by file and enclosing function. Each one
# here was read and only reads or adds the label. A new one fails the test below until
# someone checks it does not mark mail read (review round 2: a variable or a keyword
# argument hides a removal from any pattern match).
_UNREAD_ALLOWED = {
    ("dashboard/inbox.py", "_summarize_thread"),   # reads labels
    ("dashboard/inbox.py", "mark_unread"),         # adds UNREAD
    ("scripts/inbox_triage.py", "label_stats"),    # counts a label
}


def _unread_literals(paths, root):
    """(file, enclosing function, line) for every "UNREAD" string constant, parsed, so a
    comment or docstring mentioning it does not count."""
    import ast
    out = []
    for p in paths:
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in [tree] + [n for n in ast.walk(tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            name = getattr(fn, "name", "<module>")
            body = fn.body if fn is tree else [fn]
            for top in body:
                for node in ast.walk(top):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
                        continue
                    if isinstance(node, ast.Constant) and node.value == "UNREAD":
                        out.append((str(p.relative_to(root)), name, node.lineno))
    # A literal inside a nested function is reported for each enclosing scope; keep the
    # innermost name per line.
    best = {}
    for f, name, line in out:
        if (f, line) not in best or name != "<module>":
            best[(f, line)] = name
    return sorted((f, n, line) for (f, line), n in best.items())


def test_no_unreviewed_unread_literal_in_source():
    # Glen, 2026-10-11: "stop marking read". The Zelle import removed UNREAD from the bank
    # notifications it applied, from 2026-08-29.
    skip = {"tests", ".venv", "venv", "node_modules", ".git"}
    paths = [p for p in _repo().rglob("*.py") if not skip & set(p.relative_to(_repo()).parts)]
    assert len(paths) > 100
    found = _unread_literals(paths, _repo())
    unreviewed = [x for x in found if (x[0], x[1]) not in _UNREAD_ALLOWED]
    assert unreviewed == []
    assert {(f, n) for f, n, _ in found} == _UNREAD_ALLOWED   # stale entries come out


def test_the_unread_scan_catches_a_removal_however_written(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text('def f(s):\n    labels = ["UNREAD"]\n    s.modify(body={"removeLabelIds": labels})\n'
                     'def g(t):\n    _modify_thread(t, remove=["UNREAD"])\n'
                     '# "UNREAD" in a comment does not count\n')
    assert _unread_literals([probe], tmp_path) == [("probe.py", "f", 2), ("probe.py", "g", 5)]


def test_mark_read_refuses():
    with pytest.raises(RuntimeError):
        _inbox.mark_read("T1")


# ── Guard 4: IMAP readers open mailboxes read-only and only PEEK ─────────────

def _imap_marks_read(paths, root):
    """IMAP calls that would set \\Seen: select() of a named mailbox without
    readonly=True, a fetch of RFC822 or BODY[] without PEEK, or a store() adding \\Seen."""
    import ast
    out = []
    for p in paths:
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            strs = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            attr = n.func.attr
            where = f"{p.relative_to(root)}:{n.lineno}"
            if attr == "select" and strs and strs[0].upper() in ("INBOX", "[GMAIL]/ALL MAIL"):
                ro = [k for k in n.keywords if k.arg == "readonly"]
                if not (ro and isinstance(ro[0].value, ast.Constant) and ro[0].value.value is True):
                    out.append(where)
            elif attr == "fetch" and any(("RFC822" in s.upper() or "BODY[" in s.upper())
                                         and "PEEK" not in s.upper() for s in strs):
                out.append(where)
            elif attr == "store" and any("\\SEEN" in s.upper() for s in strs) \
                    and any(s.startswith("+") for s in strs):
                out.append(where)
    return out


def test_no_imap_reader_marks_mail_read():
    # Review round 1 (2026-10-11): the daily supplier-quote scan fetched RFC822 from a
    # read-write INBOX, marking every message it opened read.
    skip = {"tests", ".venv", "venv", "node_modules", ".git"}
    paths = [p for p in _repo().rglob("*.py") if not skip & set(p.relative_to(_repo()).parts)]
    assert _imap_marks_read(paths, _repo()) == []


def test_the_imap_scan_catches_each_form(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text('imap.select("INBOX")\nimap.fetch(n, "(RFC822)")\n'
                     'imap.store(n, "+FLAGS", "\\\\Seen")\n'
                     'imap.select("INBOX", readonly=True)\nimap.fetch(n, "(BODY.PEEK[])")\n')
    assert _imap_marks_read([probe], tmp_path) == ["probe.py:1", "probe.py:2", "probe.py:3"]
