"""The portal page's half of the staff guard, run in node against a fake fetch and DOM.

Spec: docs/superpowers/specs/2026-09-26-portal-staff-guard-design.md. The page tags its
own requests, asks in the page when the server answers 409 staff_confirm, re-sends once
on "Do it", and shows a banner when the portal data says staff are viewing.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "client-portal.html"
PORTAL_SCRIPTS = [PAGE, *sorted((ROOT / "static" / "js").glob("portal-*.js")),
                  ROOT / "static" / "portal-mentor.js", ROOT / "static" / "tts-output.js",
                  ROOT / "static" / "entity-ref.js"]


def _guard_source():
    page = PAGE.read_text()
    m = re.search(r"<script id=\"staff-guard\">(.*?)</script>", page, re.S)
    assert m, "the staff-guard script block is missing"
    return m.group(1)


HARNESS = r"""
const assert = require('assert');

// ── a fake DOM, just enough for the dialog and the banner ────────────────────
function el(tag){
  const e = {tagName: tag.toUpperCase(), children: [], parentNode: null, attrs: {}, listeners: {},
    textContent: '', className: '', id: '', type: '',
    classList: {_s: new Set(), add(c){ this._s.add(c); }, contains(c){ return this._s.has(c); }},
    appendChild(c){ c.parentNode = this; this.children.push(c); return c; },
    insertBefore(c, ref){ c.parentNode = this; const i = this.children.indexOf(ref);
      i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c){ this.children = this.children.filter(x => x !== c); c.parentNode = null; return c; },
    get firstChild(){ return this.children[0] || null; },
    setAttribute(k, v){ this.attrs[k] = String(v); },
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    click(){ (this.listeners.click || []).forEach(f => f({})); },
    focus(){ global.focused = this; },
  };
  return e;
}
function walk(n, out){ n.children.forEach(c => { out.push(c); walk(c, out); }); return out; }
let docListeners = {};
global.document = {
  body: el('body'),
  createElement: el,
  getElementById(id){ return walk(this.body, []).find(n => n.id === id) || null; },
  addEventListener(t, f){ (docListeners[t] = docListeners[t] || []).push(f); },
  removeEventListener(t, f){ docListeners[t] = (docListeners[t] || []).filter(x => x !== f); },
};
function dialogs(){ return walk(document.body, []).filter(n => n.className === 'staff-confirm'); }
function byClass(c){ return walk(document.body, []).find(n => (n.className || '').split(' ').includes(c)); }
function press(key){ (docListeners.keydown || []).slice().forEach(f => f({key, preventDefault(){}})); }

// ── a fake network ───────────────────────────────────────────────────────────
global.location = {href: 'https://myhealingoasis.com/portal/T', origin: 'https://myhealingoasis.com'};
let sent = [];
let replies = [];
global.window = global;
window.fetch = function(input, init){
  const h = new Headers((init && init.headers) || undefined);
  sent.push({url: String(input), headers: h, body: init && init.body});
  const next = replies.shift() || {status: 200, body: {ok: true}};
  return Promise.resolve(new Response(JSON.stringify(next.body), {status: next.status,
    headers: {'Content-Type': 'application/json'}}));
};
const STAFF_409 = {status: 409, body: {staff_confirm: {action: 'This books a real appointment and emails Mel and Rae.', client: 'Mel'}}};
const tick = () => new Promise(r => setTimeout(r, 0));

GUARD_SOURCE

(async () => {
  // 1. every same-origin request is tagged, and existing headers survive
  sent = []; replies = [];
  await fetch('/api/portal/T/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  assert.strictEqual(sent[0].headers.get('X-Portal-View'), '1');
  assert.strictEqual(sent[0].headers.get('Content-Type'), 'application/json');
  assert.strictEqual(sent[0].headers.get('X-Portal-Staff-View'), null);

  // 1b. a cross-origin request is never tagged (a custom header would force a CORS preflight)
  sent = [];
  await fetch('https://api.example.org/tts', {method: 'POST', body: 'x'});
  assert.strictEqual(sent[0].headers.get('X-Portal-View'), null);

  // 2. a 409 staff_confirm asks; "Do it" re-sends exactly once, confirmed
  sent = []; replies = [STAFF_409, {status: 200, body: {booked: true}}];
  let p = fetch('/api/onboarding/book', {method: 'POST', body: '{"slot":1}'});
  await tick(); await tick(); await tick();
  assert.strictEqual(dialogs().length, 1);
  const txt = walk(dialogs()[0], []).map(n => n.textContent).join(' | ');
  assert.ok(txt.includes('Do this as Mel?'), txt);
  assert.ok(txt.includes('This books a real appointment and emails Mel and Rae.'), txt);
  assert.strictEqual(global.focused, byClass('staff-confirm-cancel'));
  byClass('staff-confirm-go').click();
  let r = await p;
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(await r.json(), {booked: true});
  assert.strictEqual(sent.length, 2);
  assert.strictEqual(sent[1].headers.get('X-Staff-Confirmed'), '1');
  assert.strictEqual(sent[1].body, '{"slot":1}');
  assert.strictEqual(dialogs().length, 0);

  // 3. "Cancel": nothing re-sent, the caller gets a readable 409
  sent = []; replies = [STAFF_409];
  p = fetch('/api/onboarding/book', {method: 'POST', body: '{}'});
  await tick(); await tick(); await tick();
  byClass('staff-confirm-cancel').click();
  r = await p;
  assert.strictEqual(r.status, 409);
  assert.ok((await r.json()).staff_confirm);
  assert.strictEqual(sent.length, 1);

  // 3b. Escape cancels too
  sent = []; replies = [STAFF_409];
  p = fetch('/api/onboarding/book', {method: 'POST', body: '{}'});
  await tick(); await tick(); await tick();
  press('Escape');
  r = await p;
  assert.strictEqual(r.status, 409);
  assert.strictEqual(sent.length, 1);

  // 4. two refusals at once: one dialog at a time, the second after the first closes
  sent = []; replies = [STAFF_409, STAFF_409, {status: 200, body: {n: 2}}];
  const a = fetch('/api/onboarding/book', {method: 'POST', body: 'a'});
  const b = fetch('/api/onboarding/book', {method: 'POST', body: 'b'});
  await tick(); await tick(); await tick(); await tick();
  assert.strictEqual(dialogs().length, 1);
  byClass('staff-confirm-cancel').click();
  assert.strictEqual((await a).status, 409);
  await tick(); await tick(); await tick();
  assert.strictEqual(dialogs().length, 1);
  byClass('staff-confirm-go').click();
  assert.strictEqual((await b).status, 200);
  assert.strictEqual(sent.length, 3);

  // 5. the dialog cannot be built: fail closed, nothing re-sent
  const realCreate = document.createElement;
  document.createElement = () => { throw new Error('boom'); };
  sent = []; replies = [STAFF_409];
  r = await fetch('/api/onboarding/book', {method: 'POST', body: '{}'});
  document.createElement = realCreate;
  assert.strictEqual(r.status, 409);
  assert.strictEqual(sent.length, 1);

  // 6. any other 409 passes straight through
  sent = []; replies = [{status: 409, body: {error: 'slot_taken'}}];
  r = await fetch('/api/onboarding/book', {method: 'POST', body: '{}'});
  assert.strictEqual(r.status, 409);
  assert.strictEqual(dialogs().length, 0);
  assert.strictEqual(sent.length, 1);

  // 7. the banner: shown once when staff_view is present, never otherwise
  _staffBanner({});
  assert.strictEqual(document.getElementById('staffBanner'), null);
  _staffBanner({staff_view: {client: 'Mel Palmer'}});
  _staffBanner({staff_view: {client: 'Mel Palmer'}});
  const banners = walk(document.body, []).filter(n => n.id === 'staffBanner');
  assert.strictEqual(banners.length, 1);
  assert.strictEqual(banners[0].textContent,
    "You are viewing Mel Palmer's portal as staff. Anything you do here asks first.");
  assert.strictEqual(document.body.firstChild, banners[0]);

  // 8. a staff-view page marks its requests, and a lapsed staff view is refused, not run
  sent = []; replies = [{status: 409, body: {staff_expired: true}}];
  r = await fetch('/api/onboarding/book', {method: 'POST', body: '{}'});
  assert.strictEqual(sent[0].headers.get('X-Portal-Staff-View'), '1');
  assert.strictEqual(r.status, 409);
  assert.strictEqual(sent.length, 1);
  const notice = dialogs();
  assert.strictEqual(notice.length, 1);
  const ntxt = walk(notice[0], []).map(n => n.textContent).join(' | ');
  assert.ok(ntxt.includes('Your staff view has expired'), ntxt);
  assert.ok(ntxt.includes('Open the portal again from the console'), ntxt);
  assert.strictEqual(byClass('staff-confirm-go'), undefined);
  byClass('staff-confirm-cancel').click();
  assert.strictEqual(dialogs().length, 0);

  // 9. a lapsed staff view says so in the banner, on a fresh page
  document.body.children = [];
  _staffBanner({staff_view: {client: 'Mel Palmer', expired: true}});
  assert.strictEqual(document.getElementById('staffBanner').textContent,
    "Your staff view of Mel Palmer's portal has expired. Open it again from the console.");
  console.log('OK');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_staff_guard_page_behaviour(tmp_path):
    js = tmp_path / "harness.js"
    js.write_text(HARNESS.replace("GUARD_SOURCE", _guard_source()))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout


def _strip_comments(src):
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(^|[^:\"'])//[^\n]*", r"\1", src)


def test_guard_never_uses_browser_dialogs():
    """A browser confirm() blocks the page and cannot be styled or tested. The client's
    own existing confirms elsewhere on the page are out of scope."""
    src = _strip_comments(_guard_source())
    for call in (r"\bconfirm\(", r"\balert\(", r"\bprompt\("):
        assert not re.search(call, src), f"{call} appears in the staff guard"


def test_guard_script_runs_before_every_other_script():
    page = PAGE.read_text()
    first = re.search(r"<script\b[^>]*>", page)
    assert first and 'id="staff-guard"' in first.group(0), first and first.group(0)


def test_every_portal_write_goes_through_fetch():
    """XMLHttpRequest, sendBeacon and a posting <form> would skip the fetch wrapper."""
    found = []
    for path in PORTAL_SCRIPTS:
        src = _strip_comments(path.read_text())
        for pat in (r"XMLHttpRequest", r"sendBeacon\(", r"<form\b[^>]*\bmethod=[\"']?post",
                    r"<form\b[^>]*\baction=", r"\.submit\(\)"):
            for m in re.finditer(pat, src, re.I):
                found.append(f"{path.name}: {src[m.start():m.start() + 60]!r}")
    assert found == [], "\n".join(found)


CONSOLE = ROOT / "static"


def test_console_open_buttons_ask_for_a_staff_pass():
    """Only the buttons that OPEN a portal tab ask for a pass. The copy-to-send link box
    must not, or the client would be put into staff view (review rounds 1 and 2)."""
    for name in ("console-client.html", "console-biofield-reveals.html"):
        assert "staff_open=1" in (CONSOLE / name).read_text(), name
    links = (CONSOLE / "console-portal-links.html").read_text()
    assert links.count("staff_open=1") == 1
    at = links.find("staff_open=1")
    assert "Opening portal" in links[max(0, at - 1200):at]


def test_editor_preview_opens_with_a_staff_pass():
    """The Biofield portal editor's "Preview portal" link, after publish, opens the portal
    as staff for ANY click, middle-click and "open in new tab" included: its href is the
    staff route itself, with no script in the way (review rounds 1 and 2)."""
    page = (CONSOLE / "console-biofield-portal.html").read_text()
    at = page.find("const link = $('previewLink');")
    assert at != -1
    block = page[at:at + 700]
    assert "link.href = '/console/open-portal?email=' + encodeURIComponent(email)" in block
    assert "r.json.url" not in block.split("link.href", 1)[1].split(";", 1)[0]
