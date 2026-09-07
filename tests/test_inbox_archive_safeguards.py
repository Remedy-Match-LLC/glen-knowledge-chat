"""Archive is the only way mail leaves the reply queue, so it needs guards.

`archiveCurrent()` had none. One click removed the INBOX label, there was no
confirmation, and nothing anywhere in the repo re-added the label. The button
also sits directly after "Send Reply" in the action bar.

Archive itself is not destructive. It removes INBOX and nothing else, so the
thread stays in All Mail. That makes an undo cheap, and its absence was just
an omission.

Also covered: a read-only listing of the account's Gmail filters.
`scripts/inbox_triage.py` can CREATE filters that archive mail forever, with
no age guard and no starred guard, but nothing could ever read them back.
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


def _hdr():
    return {"X-Console-Key": "sek", "Content-Type": "application/json"}


# ── Undo: archive must be reversible ─────────────────────────────────────────

def test_unarchive_re_adds_the_inbox_label(monkeypatch):
    seen = {}
    monkeypatch.setattr(_inbox, "_modify_thread",
                        lambda tid, add=None, remove=None: seen.update(
                            tid=tid, add=add, remove=remove))
    _inbox.unarchive_thread("T9")
    assert seen == {"tid": "T9", "add": ["INBOX"], "remove": None}, seen


def test_unarchive_route_calls_unarchive(monkeypatch):
    _auth(monkeypatch)
    calls = []
    monkeypatch.setattr(_inbox, "unarchive_thread", lambda tid: calls.append(tid))
    r = app.app.test_client().post("/api/inbox/threads/T9/unarchive", headers=_hdr())
    assert calls == ["T9"], f"route did not unarchive, got {calls}"
    assert r.get_json().get("ok") is True, r.get_json()


def test_unarchive_route_needs_the_console_key(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(_inbox, "unarchive_thread",
                        lambda tid: pytest.fail("unarchived without a key"))
    r = app.app.test_client().post("/api/inbox/threads/T9/unarchive")
    assert r.status_code == 401, r.status_code


# ── Filters: they must be readable ───────────────────────────────────────────

def test_filters_route_lists_what_gmail_reports(monkeypatch):
    _auth(monkeypatch)
    rows = [{"id": "f1", "criteria": {"query": "category:promotions"},
             "action": {"removeLabelIds": ["INBOX"]}}]
    monkeypatch.setattr(_inbox, "list_filters", lambda: rows)
    r = app.app.test_client().get("/api/inbox/filters", headers=_hdr())
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body["data"]["filters"] == rows, body


# ── Confirmation: archiving needs a deliberate yes ───────────────────────────

def _extract_archive():
    html = (_repo() / "static" / "console-inbox.html").read_text()
    m = re.search(r"/\* === archive \(test-extracted\) === \*/(.*?)"
                  r"/\* === end archive === \*/", html, re.S)
    assert m, "archive marker block not found in console-inbox.html"
    return m.group(1)


def _js(prelude, body):
    return textwrap.dedent(prelude) + _extract_archive() + textwrap.dedent(body)


_STUBS = """
  const CALLS = [];
  const TOASTS = [];
  let CONFIRMED = false;
  let CURRENT_THREAD_ID = "T5";
  let CURRENT_THREAD = { messages: [] };
  function confirm() { return CONFIRMED; }
  async function api(path, opts) { CALLS.push(path); return {}; }
  function toast(msg, kind, action) { TOASTS.push({ msg, kind, action }); }
  function backToList() {}
  const document = { querySelector: () => null, querySelectorAll: () => [],
                     getElementById: () => null };
  function assert(c, m){ if(!c){ console.error("FAIL: " + m); process.exit(1); } }
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_declining_the_confirm_archives_nothing():
    script = _js(_STUBS, """
      CONFIRMED = false;
      archiveCurrent().then(() => {
        const arch = CALLS.filter(p => /\\/archive\\b/.test(p));
        assert(arch.length === 0, "archived despite a declined confirm: " + arch);
        console.log("OK");
      });
    """)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_accepting_the_confirm_archives_and_offers_a_working_undo():
    script = _js(_STUBS, """
      CONFIRMED = true;
      archiveCurrent().then(async () => {
        const arch = CALLS.filter(p => /\\/archive\\b/.test(p));
        assert(arch.length === 1, "expected one archive call, got: " + arch);

        const withUndo = TOASTS.filter(t => t.action && /undo/i.test(t.action.label));
        assert(withUndo.length === 1, "no Undo offered after archiving");

        await withUndo[0].action.fn();
        assert(CALLS.some(p => p === "/api/inbox/threads/T5/unarchive"),
          "Undo did not unarchive the right thread: " + JSON.stringify(CALLS));
        console.log("OK");
      });
    """)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
