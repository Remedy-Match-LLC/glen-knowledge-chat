// tests/test_portal_door_back_control.js
// Run: node tests/test_portal_door_back_control.js
//
// Final review I6. showDoor() reveals EVERY panel of a door at once, so a control
// rendered once per panel is rendered once per revealed panel. Account holds nine
// panels, so Account showed nine identical "‹ Back to hub" buttons down one scroll.
// The copy was stale as well: under the shell the hub grid is gone and Home
// replaced it.
//
// This asserts on the assembled page, not on the source, because the defect is a
// count of what renders together, and every per-panel source line looked correct
// on its own. The page is built by tests/lib/portal-render-harness.js with a
// payload that turns on every optional panel, so the doors are at their fullest.
const assert = require('assert');
const path = require('path');
const harness = require('./lib/portal-render-harness.js');
const shell = require(path.join(__dirname, '..', 'static', 'js', 'portal-shell.js'));

const BACK_CLASS = 'hub-back';

// Every optional panel on, so Account carries its full stack and My Remedies
// carries all four of its panels.
const V = {
  hub_enabled: true, shell_enabled: true,
  remedies: { enabled: true }, oasis: { enabled: true },
  orders: { visible: true, items: [{ date: '2026-08-01', total_cents: 100, status: 'paid' }] }
};
const D = { name: 'Mary Boyd', scan_history_enabled: true };

function build(opts) {
  return harness.render(harness.read(), Object.assign(
    { d: D, v: V, hub: true, window: { PortalShell: shell } }, opts));
}

function backsByDoor(html) {
  const counts = {};
  harness.sections(html).forEach(function (s) {
    const n = (s.body.match(new RegExp(BACK_CLASS, 'g')) || []).length;
    if (!n) return;
    const door = shell.doorForPanel(s.panel);
    assert.ok(door, 'panel ' + s.panel + ' carries a back control but belongs to no door');
    counts[door] = (counts[door] || 0) + n;
  });
  return counts;
}

// ---------------------------------------------------------------------------
// Under the shell: one control per door, and none on Home.
// ---------------------------------------------------------------------------
{
  const html = build({ shell: true, doors: true });
  const panels = harness.sections(html);
  // The fixture has to actually produce a crowded door, or "one per door" is
  // trivially satisfied by a door with one panel.
  const accountPanels = panels.filter(function (s) { return shell.doorForPanel(s.panel) === 'account'; });
  assert.ok(accountPanels.length >= 5,
    'the fixture must render a crowded Account door to be worth asserting on, got ' +
    accountPanels.length + ' panels');

  const counts = backsByDoor(html);
  Object.keys(counts).forEach(function (door) {
    assert.strictEqual(counts[door], 1,
      'the ' + door + ' door renders ' + counts[door] + ' back controls; showDoor reveals ' +
      'all of a door\'s panels at once, so the client sees every one of them stacked');
  });
  assert.ok(!('home' in counts),
    'Home is where the control goes, so no panel of the Home door may carry one');

  // Every door that has a panel in this render must have exactly one, otherwise a
  // door is left with no way back at all.
  const doorsPresent = {};
  panels.forEach(function (s) {
    const d = shell.doorForPanel(s.panel);
    if (d && d !== 'home') doorsPresent[d] = true;
  });
  Object.keys(doorsPresent).forEach(function (d) {
    assert.strictEqual(counts[d], 1, 'the ' + d + ' door has panels but no back control');
  });

  // Copy: it goes to Home under the shell, so it has to say so.
  assert.ok(html.indexOf('Back to Home') !== -1, 'the shell control must name Home');
  assert.ok(html.indexOf('Back to hub') === -1,
    'the hub grid does not exist under the shell, so no control may still point at it');
  assert.ok(!/[—]|--/.test('Back to Home'), 'client-facing copy uses no em dash');
}

// ---------------------------------------------------------------------------
// Shell off: unchanged. Every revealed panel is revealed alone, so it keeps its
// own control, with the original wording. The exact markup is also pinned by
// tests/test_portal_flag_off_snapshot.js against aaa78c42.
// ---------------------------------------------------------------------------
{
  const html = build({ shell: false, doors: false });
  const withBack = harness.sections(html).filter(function (s) {
    return s.body.indexOf(BACK_CLASS) !== -1;
  });
  assert.ok(withBack.length > 10,
    'with the shell off every non-hub panel keeps its own control, got ' + withBack.length);
  assert.ok(html.indexOf('Back to hub') !== -1, 'the legacy control keeps its original wording');
  assert.ok(html.indexOf('Back to Home') === -1,
    'the legacy page must not gain the shell wording');
}

// ---------------------------------------------------------------------------
// The Oasis panel replaces its own innerHTML after every Build-Out action. It
// must not mint a fresh control there: under the shell the door's control may
// belong to a different panel, and a fresh one would be both a duplicate and
// stale copy.
// ---------------------------------------------------------------------------
{
  const page = harness.read();
  const fn = /function renderOasisPanel\(freshBlock\)\s*\{[\s\S]*?\n\}/.exec(page);
  assert.ok(fn, 'renderOasisPanel() not found');
  const code = fn[0].split('\n')
    .filter(function (l) { return l.trim().slice(0, 2) !== '//'; }).join('\n');
  assert.ok(/const existingBack = panel\.querySelector\("\.hub-back"\);/.test(code),
    'renderOasisPanel() must read the control already in the panel');
  assert.ok(/existingBack \? existingBack\.outerHTML/.test(code),
    'renderOasisPanel() must re-use that control, not mint a fresh one');
  assert.ok(/portalShellLive\(\) \? "" :/.test(code),
    'renderOasisPanel() must emit no control at all under the shell when the panel had none');
  assert.ok(/panel\.innerHTML = backHtml \+ buildOasisHtml/.test(code),
    'renderOasisPanel() must render the control it resolved, not a different one');
}

console.log('test_portal_door_back_control: ok (one control per door, none on Home, legacy unchanged)');
