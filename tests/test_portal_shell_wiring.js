// tests/test_portal_shell_wiring.js
// Run: node tests/test_portal_shell_wiring.js
//
// Asserts on the page source only for structural facts that cannot be executed
// without a DOM (which sections exist, and what door each declares). The behaviour
// of showDoor is covered by executing it, below.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { DOORS, doorForPanel, allPanels } = require('../static/js/portal-shell.js');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

// every section declares the door the map assigns it, and the set of panels the
// page declares is exactly the set the door map knows about (not just "at least
// 21", which would still pass if two sections were quietly deleted).
const sections = page.match(/<section[^>]*data-panel="[a-z][a-z-]*"[^>]*>/g) || [];
const declaredPanels = Array.from(new Set(sections.map(function (tag) {
  return /data-panel="([a-z][a-z-]*)"/.exec(tag)[1];
}))).sort();
assert.deepStrictEqual(declaredPanels, allPanels().slice().sort(),
  'the page must declare a section for exactly the panels the door map knows about');
sections.forEach(function (tag) {
  const panel = /data-panel="([a-z][a-z-]*)"/.exec(tag)[1];
  const door = /data-door="([a-z][a-z-]*)"/.exec(tag);
  assert.ok(door, panel + ' section is missing data-door');
  assert.strictEqual(door[1], doorForPanel(panel),
    panel + ' declares door ' + door[1] + ' but the map says ' + doorForPanel(panel));
});

// the shell script is loaded before the inline code that calls into it
const shellTag = page.indexOf('/static/js/portal-shell.js');
assert.ok(shellTag !== -1, 'portal-shell.js is not loaded by the page');
assert.ok(shellTag < page.indexOf('function showDoor'), 'portal-shell.js loads too late');

// --- behaviour: extract showDoor and run it against a fake DOM ---------------------
// Extracted and executed rather than regex-matched. A source regex would pass on a
// commented-out implementation. The fake is selector-aware because showDoor queries
// two different things: the panels it reveals, and the rail buttons it highlights.
const src = /function showDoor\(key, options\)\{[\s\S]*?\n\}/.exec(page);
assert.ok(src, 'showDoor not found in the page');

// Panels start as render() leaves them: Home revealed, everything else hidden.
// Starting them all visible would make the transition assertions below untestable,
// since nothing would ever be a transition.
const panels = DOORS.reduce(function (all, d) {
  return all.concat(d.panels.map(function (p) {
    return { dataset: { panel: p, door: d.key }, hidden: p !== 'hub' };
  }));
}, []);
const rail = DOORS.map(function (d) {
  return { dataset: { door: d.key }, active: false,
           classList: { toggle: function (cls, on) { this.owner.active = !!on; } } };
});
rail.forEach(function (b) { b.classList.owner = b; });
const shown = [];
const document = {
  querySelectorAll: function (sel) { return sel === '[data-panel]' ? panels : rail; },
  querySelector: function () { return null; }
};
const window = { PortalShell: require('../static/js/portal-shell.js') };
const sessionStorage = { setItem: function () {} };
const panelShown = function (n) { shown.push(n); };
eval(src[0] + '\nshowDoor("remedies");');

const visible = panels.filter(function (p) { return !p.hidden; })
  .map(function (p) { return p.dataset.panel; });
assert.deepStrictEqual(visible.sort(), ['cart', 'oasis', 'remedies', 'remedy-detail'],
  'a door must reveal all of its panels and nothing else');
assert.deepStrictEqual(shown.sort(), ['cart', 'oasis', 'remedies', 'remedy-detail'],
  'every newly visible panel must get its panelShown side effect');
assert.deepStrictEqual(rail.filter(function (b) { return b.active; })
  .map(function (b) { return b.dataset.door; }), ['remedies'],
  'exactly the opened door must be highlighted in the rail');

// ---------------------------------------------------------------------------
// Final review I7: panelShown() fires on the TRANSITION into visible, not on the
// state. It is not an idempotent hook: its intake branch calls
// initPortalIntakeCard(true), which re-renders the form, so firing it for a panel
// that was already open discards whatever the client has typed but the 1.2 second
// autosave has not yet written. showDoor runs for every panel on every call, so a
// click on the door you are already on, or any background re-render, used to do
// exactly that.
// ---------------------------------------------------------------------------
{
  const before = shown.length;
  eval('showDoor("remedies");');
  assert.strictEqual(shown.length, before,
    'reopening the door you are already on must not re-fire panelShown for panels ' +
    'that were already visible; it re-fired for ' +
    JSON.stringify(shown.slice(before)));
  assert.deepStrictEqual(panels.filter(function (p) { return !p.hidden; })
    .map(function (p) { return p.dataset.panel; }).sort(),
    ['cart', 'oasis', 'remedies', 'remedy-detail'],
    'the door must stay open across a repeat call');
}
{
  // The Account door owns the intake panel, so this is the exact live path: open
  // Account, start typing, and have anything at all call showDoor('account') again.
  eval('showDoor("account");');
  const firstOpen = shown.filter(function (n) { return n === 'intake'; }).length;
  assert.strictEqual(firstOpen, 1, 'opening Account must announce the intake panel once');
  eval('showDoor("account");');
  const secondOpen = shown.filter(function (n) { return n === 'intake'; }).length;
  assert.strictEqual(secondOpen, 1,
    'a second showDoor("account") re-announced the intake panel, which re-renders ' +
    'the form and discards the client\'s un-autosaved input');
}

// panelShown's intake branch is the one that makes the above a data-loss bug
// rather than a redundant call. Pinned on comment-stripped source so a comment
// naming the call cannot satisfy it.
{
  const fn = /function panelShown\(name\)\{[\s\S]*?\n\}/.exec(page);
  assert.ok(fn, 'panelShown() not found');
  const stripped = fn[0].split('\n')
    .filter(function (l) { return l.trim().slice(0, 2) !== '//'; }).join('\n');
  assert.ok(/if \(name === 'intake'\) initPortalIntakeCard\(true\);/.test(stripped),
    'panelShown() must still be the hook that re-renders the intake form, which is ' +
    'why it may only fire on a transition');
}

// an unknown door falls back to home rather than hiding everything
eval('showDoor("nosuchdoor");');
assert.deepStrictEqual(panels.filter(function (p) { return !p.hidden; })
  .map(function (p) { return p.dataset.panel; }), ['hub'],
  'an unknown door must fall back to home');

console.log('test_portal_shell_wiring: ok');
