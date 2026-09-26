// Run: node tests/test_portal_folds_rules.js
const assert = require('assert');
const F = require('../static/js/portal-folds.js');

const ids = ['a', 'b', 'c'];

// first visit: first open, rest folded
assert.deepStrictEqual(F.resolveDoor(F.emptyState(), ids), {a: false, b: true, c: true});

// saved values win; an unlisted card takes the positional default
let s = F.setCard(F.emptyState(), 'b', false);
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: false, b: false, c: true});

// setCard does not mutate its input
const e = F.emptyState();
F.setCard(e, 'a', true);
assert.deepStrictEqual(e.cards, {});

// markSeen adds once
s = F.markSeen(F.markSeen(F.emptyState(), 'scans'), 'scans');
assert.deepStrictEqual(s.seen, ['scans']);

// fold all, then restore returns exactly the earlier layout
s = F.setCard(F.emptyState(), 'b', false);           // a open, b open, c folded
const before = F.resolveDoor(s, ids);
s = F.foldAll(s, 'scans', ids);
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: true, b: true, c: true});
assert.ok(F.canRestore(s, 'scans'));
s = F.restore(s, 'scans');
assert.deepStrictEqual(F.resolveDoor(s, ids), before);
assert.ok(!F.canRestore(s, 'scans'));

// Restore stays available after single clicks in between
s = F.foldAll(F.emptyState(), 'scans', ids);
s = F.setCard(s, 'a', false);
assert.ok(F.canRestore(s, 'scans'));
s = F.restore(s, 'scans');
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: false, b: true, c: true});

// a second Fold all keeps the FIRST saved layout
s = F.setCard(F.emptyState(), 'c', false);
const first = F.resolveDoor(s, ids);
s = F.foldAll(F.foldAll(s, 'scans', ids), 'scans', ids);
assert.deepStrictEqual(F.restore(s, 'scans').cards, Object.assign({}, first));

// foldAll only touches the ids it is given (Review Focus 2: headless cards)
s = F.foldAll(F.setCard(F.emptyState(), 'x', false), 'scans', ['a']);
assert.strictEqual(s.cards.x, false);

// doors are independent
s = F.foldAll(F.emptyState(), 'scans', ids);
assert.ok(!F.canRestore(s, 'home'));
assert.deepStrictEqual(F.restore(s, 'home'), s);

// adopt is a deep copy
const client = {cards: {a: true}, seen: ['scans'], before_fold_all: {}};
const mine = F.adopt(client);
mine.cards.a = false;
assert.strictEqual(client.cards.a, true);

// normalise tolerates junk
assert.deepStrictEqual(F.normalise(null), F.emptyState());
assert.deepStrictEqual(F.normalise({cards: 3}), F.emptyState());

console.log('OK');

// ── review round 2, 2026-09-26: the first-visit default applies on the FIRST visit only
{
  // before the page is seen: positional default
  assert.deepStrictEqual(F.resolveDoor(F.emptyState(), ['a', 'b'], 'scans'), {a: false, b: true});
  // markSeen snapshots that default, so it never re-applies
  let s = F.markSeen(F.emptyState(), 'scans', ['a', 'b']);
  assert.deepStrictEqual(s.cards, {a: false, b: true});
  // a card inserted ABOVE later does not fold the card being read
  assert.deepStrictEqual(F.resolveDoor(s, ['y', 'a', 'b'], 'scans'), {y: false, a: false, b: true});
  // a new card on a seen page arrives open
  assert.deepStrictEqual(F.resolveDoor(s, ['a', 'b', 'invoice'], 'scans'), {a: false, b: true, invoice: false});
  console.log('OK2');
}

// ── Glen, 2026-09-26, "open action cards": named cards open on the first visit ─
{
  const open = ['inv'];
  assert.deepStrictEqual(F.resolveDoor(F.emptyState(), ['a', 'inv', 'c'], 'billing', open),
                         {a: false, inv: false, c: true});
  const s = F.markSeen(F.emptyState(), 'billing', ['a', 'inv', 'c'], open);
  assert.deepStrictEqual(s.cards, {a: false, inv: false, c: true});
  // a client's own later fold of an action card is kept
  assert.strictEqual(F.resolveDoor(F.setCard(s, 'inv', true), ['a', 'inv', 'c'], 'billing', open).inv, true);
  console.log('OK3');
}
