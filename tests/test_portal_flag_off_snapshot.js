// tests/test_portal_flag_off_snapshot.js
// Run: node tests/test_portal_flag_off_snapshot.js
//
// The governing invariant of the portal-shell-ia branch: with PORTAL_SHELL_ENABLED
// unset, which is what production runs, the page render() produces must be what it
// produced at aaa78c42, the commit this branch started from. Nothing tested that.
// Seven separate one-line mutations to the shell mount block left the whole suite
// green, and the membership-card merge shipped to every production client for a
// week behind a flag that was supposed to be dark, because every test on this
// branch asserts on a feature rather than on the absence of a change.
//
// This file closes the class. It executes render()'s body against fixtures with
// the shell off and compares the result, character for character, against a
// snapshot taken from aaa78c42.
//
// HOW IT WORKS
//   render() cannot be called: it needs a document, a token, and two dozen sibling
//   builders. So the body is sliced out of the page source, from `let html = ` to
//   `app.innerHTML = html;`, and executed as a function of (d, v, _hub, _shell,
//   _doors, ...). Every helper it calls that is defined OUTSIDE that slice is
//   replaced by a stub that returns a marker string naming itself. `esc`, `money`
//   and `ICON_LOCK` are taken from the real source, because they shape output.
//   The identical stub table and the identical fixtures are used to generate the
//   snapshot, so a stubbed helper's own contents can never be the difference: only
//   the sliced code can be, which is the code this branch rewrote.
//
// WHAT THE SNAPSHOT COVERS
//   Every card in the page body, its content, and its position in the page, across
//   three layout shapes and two payloads. That is the parts array, and it is where
//   the membership merge broke. It also covers the panel wrap: which sections exist,
//   their order, their `hidden` flags, and what lands inside each.
//
// WHAT IT DOES NOT COVER
//   Anything a stub stands in for (scan history, orders, cart, shop, photo,
//   clinical record, calendar, eye-vision report, the hub grid, and the back
//   control) is compared as a marker, so a change INSIDE one of those builders is
//   invisible here. It is source text only: no DOM, no CSS, no event wiring, and
//   nothing below `app.innerHTML = html;`, so the shell mount block itself is not
//   in scope (tests/test_portal_shell_wiring.js pins that). And two deltas from
//   aaa78c42 are normalised away rather than asserted, both listed and justified
//   at NORMALISERS below.
//
// REGENERATING (only when a flag-off change is deliberate and reviewed):
//   node tests/test_portal_flag_off_snapshot.js --write-snapshot static/client-portal.html
//   The committed snapshot was written from:
//   git show aaa78c42:static/client-portal.html > /tmp/aaa78c42.html
//   node tests/test_portal_flag_off_snapshot.js --write-snapshot /tmp/aaa78c42.html
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const PAGE = path.join(__dirname, '..', 'static', 'client-portal.html');
const SNAPSHOT = path.join(__dirname, 'fixtures', 'portal-flag-off.snapshot.txt');

// ---------------------------------------------------------------------------
// Helpers the sliced body calls but does not define. Stubbed identically on both
// sides of the comparison. A name added here weakens the test, so each one is a
// builder whose own output is pinned by its own test.
// ---------------------------------------------------------------------------
const STUBS = [
  'escGreeting', 'stripSalutation', 'renderEyeVisionReport', 'buildCalendarHtml',
  'buildAppointmentProposalHtml', 'buildMembershipSummaryHtml', 'buildScanHistoryHtml',
  'buildOrdersHtml', 'backToHub', 'buildClinicalRecordHtml', 'buildCartHtml',
  'buildShopHtml', 'buildPhotoHtml'
];

function slice(src) {
  const renderAt = src.indexOf('function render(d, v){');
  assert.ok(renderAt !== -1, 'render(d, v) not found');
  const a = src.indexOf('let html = `', renderAt);
  const b = src.indexOf('app.innerHTML = html;', renderAt);
  assert.ok(a !== -1 && b > a, 'render() body bounds not found');
  const body = src.slice(a, b);
  // A slice that silently shrank is the failure mode that unpinned a sibling test
  // on this very branch. render()'s body is tens of thousands of characters.
  assert.ok(body.length > 40000, 'render() body slice is implausibly short: ' + body.length);
  assert.ok(body.indexOf('data-panel="current"') !== -1, 'slice does not reach the panel wrap');
  return body;
}

function line(src, marker) {
  const i = src.indexOf(marker);
  assert.ok(i !== -1, 'source line not found: ' + marker);
  return src.slice(i, src.indexOf('\n', i));
}

function buildRenderer(src) {
  const stubs = STUBS.map(function (n) {
    return '  var ' + n + ' = function(){ return "[[' + n + ']]"; };';
  }).join('\n');
  const code = [
    line(src, 'const esc = '),
    line(src, 'const money = '),
    line(src, 'const ICON_LOCK = '),
    '(function(d, v, _hub, _shell, _doors, seg, token, onboardingMount, hubHtml, first, badges, recSections, _ppTimer, location){',
    stubs,
    slice(src),
    '  return html;',
    '})'
  ].join('\n');
  return (0, eval)(code); // eslint-disable-line no-eval
}

// ---------------------------------------------------------------------------
// Fixtures. Two payloads x three layout shapes. The payload fields are the ones
// whose cards this branch moved between doors, plus both membership gates, since
// each gate combination fails differently.
// ---------------------------------------------------------------------------
const RICH_D = {
  name: 'Mary Boyd', first_name: 'Mary', scan_history_enabled: true,
  linked_practitioner_account: true,
  locked_rows: [
    { name: 'Neuro Magnesium', slug: 'neuro-magnesium', regular_cents: 4500, tier: '6mo' },
    { name: 'Vitamin C', slug: 'vitamin-c', regular_cents: 2200, tier: '12mo' }
  ],
  membership_upsell: {
    already_member: false, savings_cents: 1500, reorders_30d: 2, net_after_fee_cents: -300
  }
};
const RICH_V = {
  orders: { visible: true, items: [{ date: '2026-08-01T00:00:00', total_cents: 12300, status: 'paid' }] },
  caregiver_pay_enabled: true,
  caregiver_pay: { orders: [{ order_id: 7, amount_dollars: '45.00', beneficiary_name: 'Ann', items: 'Vitamin C' }] },
  upgrade: { enabled: true, offer: { title: 'Membership', price_cents: 9900, period: '/mo', blurb: 'Join', checkout_path: '/prepay', cta_label: 'Join now' } },
  supplement_review: { status: 'on' }
};
// Locked rows without the pitch: the combination that gained the upsell-card
// gradient when the merge was not gated on the shell.
const LOCKED_ONLY_D = {
  name: 'Mary Boyd', scan_history_enabled: true,
  locked_rows: RICH_D.locked_rows,
  membership_upsell: { already_member: true, savings_cents: 1500, reorders_30d: 2, net_after_fee_cents: -300 }
};

const CASES = [
  // hub off + scan history off: the `else` arm of the panel wrap.
  ['plain', Object.assign({}, RICH_D, { scan_history_enabled: false }), RICH_V, false],
  // hub off + scan history on: the old three-tab wrap.
  ['tabs', RICH_D, RICH_V, false],
  // hub on, shell off: the hub grid with every panel, and no doors.
  ['hub', RICH_D, RICH_V, true],
  // locked rows only, both wraps.
  ['tabs-locked-only', LOCKED_ONLY_D, RICH_V, false],
  ['hub-locked-only', LOCKED_ONLY_D, RICH_V, true]
];

// ---------------------------------------------------------------------------
// NORMALISERS. Two deltas from aaa78c42 are inert by construction and are removed
// before comparison, so that everything else can be compared exactly. Each is
// asserted to be inert here rather than taken on trust.
//
//  1. `data-door="..."` on the panel sections. An unread data attribute with the
//     shell off; showDoor() is the only reader and it never runs. Removing it here
//     also means the Account-door reshuffle (finder to solutions, intake and
//     records to scans) does not have to churn this snapshot.
//  2. The six `*-detail` sections the door wrap adds. With the shell off their
//     bodies are `${back}` and nothing else, because each one renders
//     `_doors ? partsFor(door) : ""`. That they are EMPTY is the real invariant,
//     so it is asserted before they are dropped: an empty hidden section renders
//     nothing, a non-empty one is a card that escaped `current`.
// ---------------------------------------------------------------------------
const DETAIL_PANELS = ['scan-report', 'billing-detail', 'remedy-detail',
                       'solutions-detail', 'account-detail', 'learn-detail'];

function normalise(html, label) {
  DETAIL_PANELS.forEach(function (panel) {
    const re = new RegExp('\\s*<section data-panel="' + panel + '"[^>]*>([\\s\\S]*?)</section>');
    const m = re.exec(html);
    if (!m) return;                       // absent in the plain/tabs wrap
    assert.strictEqual(m[1], '[[backToHub]]',
      label + ': with the shell off, <section data-panel="' + panel + '"> must hold nothing but ' +
      'the back control, otherwise a card has escaped the `current` panel. Got: ' +
      JSON.stringify(m[1].slice(0, 200)));
    html = html.replace(re, '');
  });
  return html.replace(/ data-door="[a-z-]+"/g, '');
}

function renderAll(src) {
  const render = buildRenderer(src);
  return CASES.map(function (c) {
    const label = c[0];
    const out = render(
      c[1], c[2], c[3],
      /* _shell */ false, /* _doors */ false,
      'SEG', 'TOK', null, '[[hubHtml]]',
      /* first */ 'Mary', /* badges */ [], /* recSections */ [],
      /* _ppTimer */ '', /* location */ { origin: 'https://example.test', href: 'https://example.test/portal/SEG' }
    );
    return '===== case: ' + label + ' =====\n' + normalise(out, label);
  }).join('\n');
}

// ---------------------------------------------------------------------------
// --write-snapshot <html file>
// ---------------------------------------------------------------------------
if (process.argv[2] === '--write-snapshot') {
  const from = process.argv[3];
  assert.ok(from, 'usage: --write-snapshot <path to a client-portal.html>');
  fs.writeFileSync(SNAPSHOT, renderAll(fs.readFileSync(from, 'utf8')));
  console.log('wrote ' + SNAPSHOT + ' from ' + from);
  process.exit(0);
}

// ---------------------------------------------------------------------------
// The assertion.
// ---------------------------------------------------------------------------
const expected = fs.readFileSync(SNAPSHOT, 'utf8');
assert.ok(expected.length > 20000, 'the committed snapshot is implausibly short');
const actual = renderAll(fs.readFileSync(PAGE, 'utf8'));

if (actual !== expected) {
  // Report the first divergence with context, since the strings are large.
  let i = 0;
  while (i < actual.length && i < expected.length && actual[i] === expected[i]) i++;
  const caseAt = expected.lastIndexOf('===== case: ', i);
  const caseName = expected.slice(caseAt, expected.indexOf('\n', caseAt));
  assert.fail(
    'the shell-off page no longer matches aaa78c42.\n' +
    'This branch must not change what a production client sees while\n' +
    'PORTAL_SHELL_ENABLED is unset. First divergence in ' + caseName + ', at offset ' + i + ':\n' +
    '  expected: ' + JSON.stringify(expected.slice(i - 80, i + 200)) + '\n' +
    '  actual:   ' + JSON.stringify(actual.slice(i - 80, i + 200)));
}

// ---------------------------------------------------------------------------
// The shell mount block sits BELOW `app.innerHTML = html;`, so it is outside the
// slice above and the snapshot cannot see it. It is also the single line whose
// mutation does the most damage: dropping `v.shell_enabled` from its guard mounts
// the rail, the phone header and the composer for every production client while
// the flag is off. So it gets its own assertion here, on comment-stripped source,
// because a comment naming a guard satisfies a substring check for that guard.
// ---------------------------------------------------------------------------
const pageSrc = fs.readFileSync(PAGE, 'utf8');
const code = pageSrc.split('\n')
  .filter(function (l) { return l.trim().slice(0, 2) !== '//'; })
  .join('\n');

const MOUNT_MARK = "document.body.classList.add('has-shell')";
const mountAt = code.indexOf(MOUNT_MARK);
assert.ok(mountAt !== -1, 'the shell mount block is gone: ' + MOUNT_MARK + ' not found in live code');
assert.strictEqual(code.indexOf(MOUNT_MARK, mountAt + 1), -1, 'the shell mounts in more than one place');
const guard = code.slice(Math.max(0, mountAt - 200), mountAt);
assert.ok(/if \(v && v\.shell_enabled && window\.PortalShell\) \{[^{}]*$/.test(guard),
  'the shell mount must be guarded on v.shell_enabled AND window.PortalShell, and nothing\n' +
  'may sit between that guard and the mount. Guard region was: ' + JSON.stringify(guard.slice(-160)));

['renderPhoneHeader', 'renderRail', 'renderComposer'].forEach(function (fn) {
  const calls = code.split('window.PortalShell.' + fn + '(').length - 1;
  assert.strictEqual(calls, 1, 'window.PortalShell.' + fn + '() must be called exactly once, inside the mount guard');
  assert.ok(code.indexOf('window.PortalShell.' + fn + '(') > mountAt,
    'window.PortalShell.' + fn + '() must sit after the mount guard, not outside it');
});

console.log('test_portal_flag_off_snapshot: ok (' + CASES.length +
            ' cases, ' + expected.length + ' chars pinned against aaa78c42)');
