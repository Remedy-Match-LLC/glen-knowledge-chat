// tests/test_portal_chat_route.js
// Run: node tests/test_portal_chat_route.js
//
// The Plan report tells clients to "let us know" in the portal chat, linking to
// /portal/me#chat (Glen, 2026-10-20). This runs the REAL applyPortalHash, routes
// table and portalLoginNext from static/client-portal.html against a small DOM for
// every layout a client can be in: hub, legacy, shell, and the Terms gate. #intake
// is checked in the same layouts. A source grep would pass on a commented-out route.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

const start = page.indexOf('function portalShellLive(){');
const end = page.indexOf('window.addEventListener("hashchange", applyPortalHash);');
assert.ok(start !== -1 && end > start, 'hash route region not found');
const nextFn = /function portalLoginNext\(\)\{[\s\S]*?\n\}/.exec(page);
assert.ok(nextFn, 'portalLoginNext() not found');
const SRC = page.slice(start, end) + '\n' + nextFn[0] +
  '\nthis.applyPortalHash = applyPortalHash; this.portalLoginNext = portalLoginNext;' +
  '\nthis.ROUTES = PORTAL_HASH_ROUTES;';

function El(id) {
  return {
    id: id, attrs: {}, focused: 0, scrolled: 0,
    setAttribute(k, v) { this.attrs[k] = v; },
    scrollIntoView() { this.scrolled++; },
    focus() { this.focused++; doc.active = this; }
  };
}
let doc;

// panels: the data-panel sections present; ids: the elements present.
function run(hash, panels, ids, opts) {
  opts = opts || {};
  const reg = {};
  ids.forEach(function (id) { reg[id] = El(id); });
  doc = {
    active: null,
    getElementById(id) { return reg[id] || null; },
    querySelector(sel) {
      const m = /^\[data-panel="([a-z-]+)"\]$/.exec(sel);
      return m && panels.indexOf(m[1]) !== -1 ? { panel: m[1] } : null;
    }
  };
  const calls = { showTab: [], opened: [] };
  const win = {
    __portalView: opts.shell ? { shell_enabled: true } : {},
    PortalShell: opts.shell ? {} : undefined,
    setTimeout(fn) { fn(); }
  };
  const ctx = {
    document: doc, window: win, location: { hash: hash, href: 'https://x/portal/me' + hash },
    showTab(p) { calls.showTab.push(p); },
    _foldOpenCard(id) { calls.opened.push(id); },
    URL: URL, decodeURIComponent: decodeURIComponent, encodeURIComponent: encodeURIComponent
  };
  ctx.window.document = doc;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  const first = ctx.applyPortalHash();
  const focusedFirst = doc.active && doc.active.id;
  // render() calls applyPortalHash() again after most actions; it must not re-open
  // the card or take the cursor a second time.
  const openedBefore = calls.opened.length;
  doc.active = null;
  if (opts.rerender) ctx.applyPortalHash();
  return { ok: first, calls: calls, reg: reg, focused: focusedFirst, win: win,
           openedOnRerender: calls.opened.length - openedBefore,
           focusedOnRerender: doc.active && doc.active.id, ctx: ctx };
}

// The route exists and asks to open its card.
{
  const r = run('#chat', ['hub', 'current', 'ask'], ['chatCard', 'chatInput']);
  assert.ok(r.ctx.ROUTES.chat, '#chat is not a portal route');
  assert.strictEqual(r.ctx.ROUTES.chat.target, 'chatCard');
}

// Hub layout: the Messages card sits in the ask panel.
{
  const r = run('#chat', ['hub', 'current', 'ask'], ['chatCard', 'chatInput'], { rerender: true });
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(r.calls.showTab, ['ask', 'ask'], 'hub: #chat must open the ask panel');
  assert.deepStrictEqual(r.calls.opened, ['chatCard'], 'hub: the chat card must be opened once');
  assert.strictEqual(r.win._foldDeepLinkOpen, 'chatCard',
    'the fold state that loads later must open the chat card too');
  assert.ok(r.reg.chatCard.scrolled >= 1, 'the page must scroll to the chat card');
  assert.strictEqual(r.focused, 'chatInput', 'hub: the cursor must land in the chat box');
  assert.strictEqual(r.openedOnRerender, 0, 'a re-render must not re-open a card the client folded');
  assert.strictEqual(r.focusedOnRerender, null, 'a re-render must not take the cursor back');
}

// Legacy layout (no hub): no ask panel, the card renders in `current`.
{
  const r = run('#chat', ['current', 'intake'], ['chatCard', 'chatInput']);
  assert.deepStrictEqual(r.calls.showTab, ['current'], 'legacy: #chat must fall back to current');
  assert.deepStrictEqual(r.calls.opened, ['chatCard']);
  assert.strictEqual(r.focused, 'chatInput');
}

// Shell layout: the composer moved to the top; the card has its own box, which wins.
{
  const r = run('#chat', ['hub', 'current', 'ask'], ['chatCard', 'chatCardInput', 'chatInput'],
                { shell: true });
  assert.deepStrictEqual(r.calls.showTab, ['ask']);
  assert.strictEqual(r.focused, 'chatCardInput', 'shell: the card\'s own box takes the cursor');
}

// Case and a clicked link: #Chat works, and a click (userAction) re-opens.
{
  const r = run('#Chat', ['current'], ['chatCard', 'chatInput']);
  assert.deepStrictEqual(r.calls.opened, ['chatCard']);
}

// Terms gate: no panels, only the Terms card. #chat leads to it, as #intake does.
['#chat', '#intake'].forEach(function (h) {
  const r = run(h, [], ['portal-tos-card']);
  assert.strictEqual(r.ok, true, h + ' under the Terms gate must be handled');
  assert.deepStrictEqual(r.calls.showTab, [], h + ': the gate shows no panel');
  assert.strictEqual(r.focused, 'portal-tos-card', h + ': the Terms card takes the focus');
});

// #intake in the hub and legacy layouts: its panel exists in both.
[['hub', 'current', 'intake', 'ask'], ['current', 'intake']].forEach(function (panels) {
  const r = run('#intake', panels, ['portal-intake-card']);
  assert.deepStrictEqual(r.calls.showTab, ['intake'], '#intake must open its panel in ' + panels);
  assert.ok(r.reg['portal-intake-card'].scrolled >= 1);
});

// Both layouts actually render the card the route names.
{
  assert.ok(/if \(_hub\) \{ _askHtml \+= _askCard; \} else \{ part\(null, _askCard\); \}/.test(page),
    'the Messages card must render in both the hub and legacy layouts');
  assert.ok(/<section data-panel="ask"[^>]*>\$\{_back\("ask"\)\}\$\{_askHtml\}<\/section>/.test(page),
    'the ask panel must hold the Messages card');
  assert.ok(page.indexOf('const _askCard = `<div class="card" id="chatCard"') !== -1);
}

// portalLoginNext: only a known route key leaves the page, as ?next=.
{
  const cases = [['#chat', '?next=chat'], ['#CHAT', '?next=chat'], ['#intake', '?next=intake'],
                 ['', ''], ['#nope', ''], ['#https://evil.example', ''],
                 ['#%2F%2Fevil.example', ''], ['#%E0%A4%A', ''], ['#constructor', ''],
                 ['#__proto__', '']];
  cases.forEach(function (c) {
    const r = run(c[0], [], []);
    assert.strictEqual(r.ctx.portalLoginNext(), c[1], 'portalLoginNext for ' + JSON.stringify(c[0]));
  });
}

// A signed-out visitor is sent to sign in WITH the route.
assert.ok(page.indexOf('if(isMe){ location.href = "/portal/login" + portalLoginNext(); return; }') !== -1,
  'notFound() must carry the hash to the login page');

console.log('test_portal_chat_route: ok');
