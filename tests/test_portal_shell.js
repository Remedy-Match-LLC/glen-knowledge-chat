// tests/test_portal_shell.js
// Run: node tests/test_portal_shell.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { DOORS, panelsForDoor, doorForPanel, allPanels } = require('../static/js/portal-shell.js');

// seven doors, in rail order
assert.strictEqual(DOORS.length, 7);
assert.deepStrictEqual(DOORS.map(d => d.key),
  ['home', 'scans', 'solutions', 'remedies', 'billing', 'learn', 'account']);
DOORS.forEach(d => {
  assert.ok(d.label && typeof d.label === 'string', d.key + ' needs a label');
  assert.ok(d.icon && d.icon.indexOf('<') === -1, d.key + ' icon is path data, not markup');
  assert.ok(Array.isArray(d.panels) && d.panels.length, d.key + ' needs panels');
});

// no em dashes in any client-facing label
DOORS.forEach(d => {
  assert.ok(!/—|--/.test(d.label), d.key + ' label must not contain an em dash');
});

// membership resolves both ways
assert.deepStrictEqual(panelsForDoor('billing'), ['orders']);
assert.deepStrictEqual(panelsForDoor('nope'), []);
assert.strictEqual(doorForPanel('voice'), 'scans');
assert.strictEqual(doorForPanel('cart'), 'remedies');
assert.strictEqual(doorForPanel('nope'), null);

// every panel belongs to exactly one door
const seen = {};
allPanels().forEach(p => {
  assert.ok(!seen[p], p + ' is claimed by more than one door');
  seen[p] = true;
});

// THE load-bearing assertion: the door map and the page agree, as SETS.
// A door pointing at a panel that does not exist silently bounces to the hub,
// and a panel no door claims is unreachable. Counting is not enough; compare
// the sets so both directions fail loudly.
const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const inPage = Array.from(new Set(
  (page.match(/data-panel="[a-z]+"/g) || []).map(s => s.slice(12, -1))
)).sort();
const inMap = allPanels().slice().sort();
assert.deepStrictEqual(inMap, inPage,
  'door map and [data-panel] sections disagree.\n  map: ' + inMap.join(',') +
  '\n  page: ' + inPage.join(','));

const { renderRail, renderPhoneHeader, escapeHtml } = require('../static/js/portal-shell.js');

const rail = renderRail('billing', { open: false });
// one control per door, each addressable by its key
DOORS.forEach(d => {
  assert.ok(rail.indexOf('data-door="' + d.key + '"') !== -1, 'rail is missing ' + d.key);
  assert.ok(rail.indexOf('>' + escapeHtml(d.label) + '<') !== -1,
    'rail is missing the label for ' + d.key);
});
// exactly one active door, and it is the one asked for
assert.strictEqual((rail.match(/is-active/g) || []).length, 1);
assert.ok(/data-door="billing"[^>]*class="[^"]*is-active/.test(rail) ||
          /class="[^"]*is-active[^"]*"[^>]*data-door="billing"/.test(rail));
// collapsed by default, open when asked
assert.ok(rail.indexOf('is-open') === -1);
assert.ok(renderRail('home', { open: true }).indexOf('is-open') !== -1);
// labels are always in the DOM, hidden by CSS, so screen readers keep them
assert.ok(rail.indexOf('aria-label="Portal sections"') !== -1);

const header = renderPhoneHeader();
assert.ok(header.indexOf('data-shell-open="1"') !== -1);
assert.ok(header.indexOf('aria-label="Open the menu"') !== -1);

// escaping is real, not decorative
assert.strictEqual(escapeHtml('<img src=x onerror=alert(1)>'),
  '&lt;img src=x onerror=alert(1)&gt;');
assert.strictEqual(escapeHtml(null), '');

console.log('test_portal_shell: ok');
