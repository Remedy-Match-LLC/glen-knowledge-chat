// tests/test_portal_upsell_card_gates.js
// Run: node tests/test_portal_upsell_card_gates.js
//
// Task 11 (re-scoped): "More savings ahead" (d.locked_rows) and "Everything your
// membership unlocks" (d.membership_upsell) were merged into one
// `<div class="card upsell-card">`, emitted by a single `part("account", ...)`
// call. The two payloads keep independent gates:
//   pitch:  d.membership_upsell && !d.membership_upsell.already_member &&
//           (d.membership_upsell.savings_cents||0) > 0
//   locked: Array.isArray(d.locked_rows) && d.locked_rows.length
// A naive merge drops one payload whenever only its own gate holds. That is the
// failure this file exists to catch, so each of the 4 gate combinations gets an
// explicit assertion on what renders AND what does not, rather than being
// inferred from a source grep (which cannot tell which branch ran).
//
// The builder is extracted from static/client-portal.html and EXECUTED, not
// grepped, using the real `esc`, `money` and `ICON_LOCK` source lines so the
// executed code is faithful to what ships.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

function extractLine(marker) {
  const start = page.indexOf(marker);
  assert.ok(start !== -1, 'could not find: ' + marker);
  const end = page.indexOf('\n', start);
  return page.slice(start, end);
}

const escLine = extractLine('const esc = ');
const moneyLine = extractLine('const money = ');
const iconLockLine = extractLine('const ICON_LOCK = ');

const blockStart = page.indexOf('// Task 11 (re-scoped):');
assert.ok(blockStart !== -1, 'Task 11 merged block not found (comment marker moved?)');
const blockEnd = page.indexOf('// Order history', blockStart);
assert.ok(blockEnd !== -1 && blockEnd > blockStart, 'end marker not found after Task 11 block');
const block = page.slice(blockStart, blockEnd);

// Sanity: make sure we captured the actual merged `if`, not an empty slice.
assert.ok(block.indexOf('part("account"') !== -1, 'extracted block has no part("account", ...) call');
assert.ok(block.indexOf('hasLockedRows') !== -1 && block.indexOf('hasMembershipPitch') !== -1,
  'extracted block is missing the expected gate variables');

const fnSrc = `
${escLine}
${moneyLine}
${iconLockLine}
function build(d){
  const parts = [];
  const part = (door, html) => { parts.push({door: door, html: html}); };
  ${block}
  return parts;
}
build;
`;

const build = (0, eval)(fnSrc); // eslint-disable-line no-eval

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const LOCKED_ROWS = [
  { name: "Neuro Magnesium", slug: "neuro-magnesium", regular_cents: 4500, tier: "6mo" },
  { name: "Vitamin C", slug: "vitamin-c", regular_cents: 2200, tier: "12mo" }
];

function membershipUpsell(overrides) {
  return Object.assign({
    already_member: false,
    savings_cents: 1500,
    reorders_30d: 2,
    net_after_fee_cents: -300 // covers the fee
  }, overrides || {});
}

function run(d) {
  const parts = build(d);
  const accountParts = parts.filter(p => p.door === "account");
  return { parts, accountParts };
}

// ---------------------------------------------------------------------------
// 1. Neither gate holds: no card at all.
// ---------------------------------------------------------------------------
{
  const { parts } = run({});
  assert.strictEqual(parts.length, 0, 'neither gate: no card should render');
}
{
  // locked_rows present but empty, membership_upsell absent
  const { parts } = run({ locked_rows: [] });
  assert.strictEqual(parts.length, 0, 'empty locked_rows + no upsell: no card should render');
}

// ---------------------------------------------------------------------------
// 2. Pitch only: locked_rows absent/empty, membership_upsell gate holds.
// ---------------------------------------------------------------------------
{
  const { accountParts } = run({ membership_upsell: membershipUpsell() });
  assert.strictEqual(accountParts.length, 1, 'pitch only: exactly one account card');
  const html = accountParts[0].html;
  assert.ok(html.indexOf('<h2>Everything your membership unlocks</h2>') !== -1,
    'pitch only: heading must be the membership-pitch heading');
  assert.ok(html.indexOf('<h2>More savings ahead</h2>') === -1,
    'pitch only: locked-rows heading must not appear');
  assert.ok(html.indexOf('upsell-benefits') !== -1, 'pitch only: benefits list must render');
  assert.ok(html.indexOf('Become a member') !== -1, 'pitch only: CTA button must render');
  assert.ok(html.indexOf('enough to cover your membership on remedies alone') !== -1,
    'pitch only: save line (fee-covering wording) must render');
  assert.ok(html.indexOf('lockedrow') === -1,
    'pitch only: no locked-rows payload should be present when locked_rows is absent');
  assert.ok(html.indexOf('class="card upsell-card"') !== -1, 'card must carry the upsell-card class');
}

// ---------------------------------------------------------------------------
// 3. Locked-rows only: membership_upsell gate fails (already_member true),
//    locked_rows gate holds. THIS is the case a naive merge is most likely to
//    lose, since the old code's second `if` was keyed only on membership_upsell.
// ---------------------------------------------------------------------------
{
  const { accountParts } = run({
    locked_rows: LOCKED_ROWS,
    membership_upsell: membershipUpsell({ already_member: true })
  });
  assert.strictEqual(accountParts.length, 1, 'locked only: exactly one account card');
  const html = accountParts[0].html;
  assert.ok(html.indexOf('<h2>More savings ahead</h2>') !== -1,
    'locked only: heading must be the locked-rows heading');
  assert.ok(html.indexOf('<h2>Everything your membership unlocks</h2>') === -1,
    'locked only: membership-pitch heading must not appear');
  assert.ok(html.indexOf('Neuro Magnesium') !== -1 && html.indexOf('Vitamin C') !== -1,
    'locked only: BOTH locked rows must render, not dropped');
  assert.ok(html.indexOf('lockedrow') !== -1, 'locked only: locked-rows markup must render');
  assert.ok(html.indexOf('upsell-benefits') === -1,
    'locked only: benefits list must NOT render (that payload did not gate open)');
  assert.ok(html.indexOf('Become a member') === -1,
    'locked only: CTA button must NOT render (it belongs to the pitch half)');
}
// Also cover the case where membership_upsell is simply absent, not a
// false-gated object.
{
  const { accountParts } = run({ locked_rows: LOCKED_ROWS });
  assert.strictEqual(accountParts.length, 1, 'locked only (no upsell key at all): one card');
  const html = accountParts[0].html;
  assert.ok(html.indexOf('<h2>More savings ahead</h2>') !== -1);
  assert.ok(html.indexOf('upsell-benefits') === -1);
  assert.ok(html.indexOf('Become a member') === -1);
}

// ---------------------------------------------------------------------------
// 4. Both gates hold: one card, both payloads present, in the specified order
//    (pitch benefits/save-line, then locked rows, then the CTA button).
// ---------------------------------------------------------------------------
{
  const { accountParts } = run({
    locked_rows: LOCKED_ROWS,
    membership_upsell: membershipUpsell()
  });
  assert.strictEqual(accountParts.length, 1, 'both gates: exactly one account card, not two');
  const html = accountParts[0].html;
  assert.ok(html.indexOf('<h2>Everything your membership unlocks</h2>') !== -1,
    'both gates: heading must be the membership-pitch heading');
  assert.ok(html.indexOf('upsell-benefits') !== -1, 'both gates: benefits list must render');
  assert.ok(html.indexOf('Neuro Magnesium') !== -1 && html.indexOf('Vitamin C') !== -1,
    'both gates: locked rows must still render alongside the pitch');
  assert.ok(html.indexOf('Become a member') !== -1, 'both gates: CTA button must render');

  const benefitsIdx = html.indexOf('upsell-benefits');
  const lockedIdx = html.indexOf('lockedrow');
  const btnIdx = html.indexOf('Become a member');
  assert.ok(benefitsIdx < lockedIdx, 'order: benefits/save-line must precede the locked-rows list');
  assert.ok(lockedIdx < btnIdx, 'order: locked-rows list must precede the "Become a member" button');
}

// ---------------------------------------------------------------------------
// 5. Verbatim copy check: the "covers your membership" vs shortfall wording is
//    preserved exactly, keyed off net_after_fee_cents, independent of locked_rows.
// ---------------------------------------------------------------------------
{
  const { accountParts } = run({
    membership_upsell: membershipUpsell({ net_after_fee_cents: 250, reorders_30d: 1, savings_cents: 900 })
  });
  const html = accountParts[0].html;
  assert.ok(html.indexOf('You ordered once in the last 30 days') !== -1,
    'singular "once" wording must be preserved');
  assert.ok(html.indexOf('every month you reorder') !== -1,
    'shortfall wording (net_after_fee_cents > 0) must be preserved');
  assert.ok(html.indexOf('enough to cover your membership on remedies alone') === -1,
    'fee-covering wording must NOT appear when the fee is not covered');
}

console.log('test_portal_upsell_card_gates: ok (4 gate combinations + copy + order asserted)');
