// tests/test_portal_solutions_door_order.js
// Run: node tests/test_portal_solutions_door_order.js
//
// The Find Solutions door's DOM order is what showDoor() displays: it toggles
// `hidden` on every panel of a door without moving anything in the page, so the
// panel that comes first in the page's markup is the panel a client sees first
// on screen. At 1392px, the gap between the top of the shop panel and
// #workingOnCard (inside solutions-detail) measured about 1,090px, roughly 1.2
// screens: a client opening Find Solutions to say what they are working on had
// to scroll past "Shop wellness products" and "Find a Practitioner Near You"
// merchandising before reaching the card that asks what they need and returns
// their matched remedies.
//
// This pins the fix: solutions-detail, which carries #workingOnCard, must
// render before shop and finder in the panel wrap, so the client's own need
// leads the door.
const assert = require('assert');
const path = require('path');
const harness = require('./lib/portal-render-harness.js');
const shell = require(path.join(__dirname, '..', 'static', 'js', 'portal-shell.js'));
const conditions = require(path.join(__dirname, '..', 'static', 'js', 'portal-conditions.js'));

const V = { hub_enabled: true, shell_enabled: true };
const D = { name: 'Mary Boyd' };

const html = harness.render(harness.read(), {
  d: D, v: V, hub: true, shell: true, doors: true,
  window: { PortalShell: shell, PortalConditions: conditions }
});

const panels = harness.sections(html).map(function (s) { return s.panel; });

const detailIdx = panels.indexOf('solutions-detail');
const shopIdx = panels.indexOf('shop');
const finderIdx = panels.indexOf('finder');

assert.ok(detailIdx !== -1, 'solutions-detail panel not rendered');
assert.ok(shopIdx !== -1, 'shop panel not rendered');
assert.ok(finderIdx !== -1, 'finder panel not rendered');

assert.ok(detailIdx < shopIdx,
  'solutions-detail (index ' + detailIdx + ', carries #workingOnCard) must come before ' +
  'shop (index ' + shopIdx + ') in DOM order, so a client sees what they are working on ' +
  'before merchandising when the Find Solutions door opens');
assert.ok(detailIdx < finderIdx,
  'solutions-detail (index ' + detailIdx + ', carries #workingOnCard) must come before ' +
  'finder (index ' + finderIdx + ') in DOM order, so a client sees what they are working on ' +
  'before the practitioner finder when the Find Solutions door opens');

console.log('test_portal_solutions_door_order: ok (solutions-detail leads shop and finder)');
