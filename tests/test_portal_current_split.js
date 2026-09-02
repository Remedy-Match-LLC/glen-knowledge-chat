// tests/test_portal_current_split.js
// Run: node tests/test_portal_current_split.js
//
// Task 10 (portal-shell-ia): the oversized `current` panel is split so every card
// lands in the door that owns it. A client who taps "My Analysis" to read their
// biofield report should not scroll past a wishlist to reach it.
//
// This file asserts on source structure rather than on rendered output, because
// render() cannot be executed without most of a DOM. But it does NOT do it with
// substring greps over the whole file, which is how a test in this plan already
// passed for the wrong reason. It parses the region into push statements, using a
// real string/template state machine, and asks of each card which push emits it and
// which door that push names. A card in the wrong door, a card pushed twice, a card
// dropped, and a card still appended to the legacy builder are all distinguishable.
//
// What this file CANNOT see is a card whose surrounding `if` was dropped along with
// it: the push would still be there, tagged correctly. That is the specific way this
// task fails, and it is covered by tests/test_portal_biofield_block_reveal.py,
// tests/test_portal_offers.py and tests/test_portal_reorder_ui.py, which assert on
// the conditionals themselves.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

// ---------------------------------------------------------------------------
// The region, found structurally. A hardcoded line range is what produced two
// wrong inventories of this same code (27 cards, then 29; it is 42 and a footer).
// ---------------------------------------------------------------------------
const renderAt = page.indexOf('function render(d, v){');
assert.ok(renderAt !== -1, 'render() not found');
const regionStart = page.indexOf('let html = `', renderAt);
const regionEnd = page.indexOf('const _wrapPanels =', renderAt);
assert.ok(regionStart !== -1 && regionEnd > regionStart, 'region bounds not found');
const region = page.slice(regionStart, regionEnd);

// ---------------------------------------------------------------------------
// End of a JS statement, honouring strings, template literals and ${} nesting.
// A naive "next semicolon" would stop inside `style="...;..."` on almost every
// card here, so this has to be a real scan.
// ---------------------------------------------------------------------------
function endOfStatement(src, i) {
  const stack = [{ kind: 'code', depth: 0 }];
  while (i < src.length) {
    const c = src[i];
    const top = stack[stack.length - 1];
    if (top.kind === 'code' || top.kind === 'tplexpr') {
      if (c === '`') stack.push({ kind: 'tpl' });
      else if (c === "'") stack.push({ kind: 'sq' });
      else if (c === '"') stack.push({ kind: 'dq' });
      else if (c === '/' && src[i + 1] === '/') { i = src.indexOf('\n', i); if (i === -1) break; }
      else if (c === '(' || c === '[' || c === '{') top.depth++;
      else if (c === ')' || c === ']' || c === '}') {
        if (c === '}' && top.kind === 'tplexpr' && top.depth === 0) stack.pop();
        else top.depth--;
      } else if (c === ';' && top.kind === 'code' && top.depth === 0 && stack.length === 1) {
        return i;
      }
    } else if (top.kind === 'tpl') {
      if (c === '\\') i++;
      else if (c === '`') stack.pop();
      else if (c === '$' && src[i + 1] === '{') { stack.push({ kind: 'tplexpr', depth: 0 }); i++; }
    } else {                                     // 'sq' | 'dq'
      if (c === '\\') i++;
      else if ((top.kind === 'sq' && c === "'") || (top.kind === 'dq' && c === '"')) stack.pop();
    }
    i++;
  }
  return -1;
}

// ---------------------------------------------------------------------------
// Every push into the page body, in source order, with the door it names and the
// slice of source it owns (from the end of the previous push). A card built by a
// helper above its push, as in `let s = ...; part("scans", s);`, is found in that
// slice, which is why the window and not just the statement is captured.
// ---------------------------------------------------------------------------
const pushes = [];
// The door pattern admits hyphens: "chrome-top" is a tag, and a pattern of [a-z]+ is
// exactly the defect that let six hyphenated panels pass unnoticed in the shell suite.
const re = /(?:(?<![A-Za-z0-9_$.])part\(\s*(null|"[a-z][a-z-]*")\s*,)|(?:(?<![A-Za-z0-9_$.])html\s*\+=)/g;
let m, prevEnd = 0;
while ((m = re.exec(region)) !== null) {
  // Scan from the start of the token, not past it: for a part(...) push the
  // terminating `;` sits outside the call's parens, so a scan that began after the
  // comma would see that `)` close a paren it never opened.
  const end = endOfStatement(region, m.index);
  assert.ok(end !== -1, 'unterminated statement at offset ' + m.index);
  pushes.push({
    door: m[1] === undefined ? '<html +=>' : (m[1] === 'null' ? null : m[1].slice(1, -1)),
    legacy: m[1] === undefined,
    text: region.slice(m.index, end + 1),
    window: region.slice(prevEnd, end + 1)
  });
  prevEnd = end + 1;
  re.lastIndex = end + 1;
}

// The parse itself must be trustworthy before anything is concluded from it.
// Task 11 (re-scoped) merged the "More savings ahead" and "Everything your
// membership unlocks" pushes into one, but only under the shell (final review C1),
// so all three pushes exist in source: the merged card plus the two originals,
// which is 65, one more than the 64 this region held before the branch.
assert.strictEqual(pushes.length, 65,
  'expected 65 pushes into the page body, parsed ' + pushes.length);

// ---------------------------------------------------------------------------
// Card -> door. Each signature is markup or a call unique to one card. Several
// cards are emitted by more than one push (the options card is opened, branched
// three ways and closed; the consult card has three exclusive branches), so these
// are signatures, not a card count: 42 cards and a footer live in this region.
// ---------------------------------------------------------------------------
const SIGS = [
  // --- Scans & Reports ---
  ['scans', 'id="biofield-order-card"'],                       // Order your first Biofield Analysis
  ['scans', 'renderEyeVisionReport(d.eye_vision_report)'],     // Eye & Vision report
  ['scans', 'window.__hhGo(this.value)'],                      // household scan switcher
  ['scans', '<h2>Your personal message from Dr. Glen</h2>'],
  ['scans', '<h2>Your audio walkthrough</h2>'],
  ['scans', '<h2>Your written report</h2>'],
  ['scans', '<h2>Preparing your Biofield Analysis'],
  ['scans', '<h2>Curious what your body is asking for?</h2>'],
  ['scans', '<h2>Your scan analysis</h2>'],
  ['scans', 'class="rr-header"'],                              // read-receipt report variant
  ['scans', '<h2>Your healing path</h2>'],
  ['scans', 'class="card scanrec-card"'],                      // What your scan matched
  ['scans', '<h2>Your formulation matches</h2>'],
  ['scans', '<h2 style="font-size:1rem">Scan history</h2>'],
  ['scans', 'Join your consult'],                              // Biofield Consult, booked
  ['scans', 'Schedule your 30 minute consult'],                // Biofield Consult, ready
  ['scans', 'unlocks once your Causal report'],                // Biofield Consult, not yet

  // --- Billing ---
  ['billing', '<h2>Your invoice</h2>'],
  ['billing', '<h2>Your options &amp; pricing</h2>'],
  ['billing', 'data-fp-cancel="1"'],                           // Family Plan, active
  ['billing', 'data-fp-subscribe="1"'],                        // Family Plan, offered
  ['billing', 'No monthly subscription'],                      // Family Plan, absent
  ['billing', 'class="card history-card"'],                    // History & receipts
  ['billing', '<h2>Your orders</h2>'],

  // --- My Remedies ---
  ['remedies', 'supportProgramCardHtml(d.support_program)'],   // support program
  ['remedies', '<h2>Your Life Stress Essences</h2>'],
  ['remedies', '<h2>Premier Research Labs options</h2>'],
  ['remedies', '<h2>Fullscript</h2>'],
  ['remedies', 'class="card practitioner-program-card"'],
  ['remedies', 'id="portal-order-basket"'],                    // Order your remedies
  ['remedies', '<h2>Your Remedies</h2>'],
  ['remedies', 'id="wishlistCard"'],

  // --- Find Solutions ---
  ['solutions', '<h2>Your practitioner recommends</h2>'],
  ['solutions', 'renderRecommendations(recSections)'],         // My Recommendations

  // --- Account ---
  ['account', '<h2>Your practitioner account</h2>'],
  ['account', '<h2>See everything your membership unlocks</h2>'],
  ['account', 'class="card sharing-card"'],                    // Share & Unlock
  // Task 11 (re-scoped): the locked-rows nudge and the membership pitch are
  // unified into a single card with two independently gated halves. Both
  // signatures below resolve to the same push, which is expected: each
  // signature only has to be unique to that one push, not to each other.
  ['account', 'class="card upsell-card"><h2>${heading}</h2>'],
  ['account', 'hasMembershipPitch ? "Everything your membership unlocks" : "More savings ahead"'],
  // ...and the two originals, which is what a shell-off page still renders. They
  // are separate pushes, so a future merge that forgets the `_doors` gate again
  // removes these two signatures and this file goes red.
  ['account', 'class="card"><h2>More savings ahead</h2>'],
  ['account', 'class="card upsell-card"><h2>Everything your membership unlocks</h2>'],
  ['account', '<h2>Free Product Review</h2>'],
  ['account', 'class="card notifpref quiet"'],                 // notification preference
  ['account', '<h2 style="font-size:1rem">Sharing</h2>'],
  ['account', '<h2 style="font-size:1rem">Family notifications</h2>'],
  ['account', 'id="scanPrefsCard"'],                           // Your preferences

  // --- Learn & Ask ---
  // Three hidden placeholders that initOnboardingCard/initCoachesCard/initPeerCard
  // fill after the markup is attached. Learn & Ask owns onboarding, coaches and
  // peer matching, so they get their own section there rather than being buried in
  // a door that does not own them.
  ['learn', 'id="onboarding-card"'],
  ['learn', 'id="coaches-card"'],
  ['learn', 'id="peer-card"'],

  // --- page chrome, no door ---
  // Neither belongs to a door, but they belong at opposite ends of the page, so they
  // are tagged separately and rendered into two containers. A single chrome tag put
  // the practitioner's co-brand band below every card on every door.
  ['chrome-top', 'class="card your-practitioner-band"'],       // practitioner co-brand band
  ['chrome-bottom', 'class="foot"']                            // With aloha, Dr. Glen & Rae
];

const doorOfSig = {};
SIGS.forEach(function (row) {
  const door = row[0], sig = row[1];
  const owners = pushes.filter(function (p) { return p.window.indexOf(sig) !== -1; });
  assert.strictEqual(owners.length, 1,
    'signature ' + JSON.stringify(sig) + ' should be emitted by exactly one push, found ' +
    owners.length);
  const owner = owners[0];
  assert.ok(!owner.legacy,
    JSON.stringify(sig) + ' is still appended to the legacy `current` builder with `html +=`');
  assert.strictEqual(owner.door, door,
    JSON.stringify(sig) + ' is pushed to door ' + JSON.stringify(owner.door) +
    ', expected ' + JSON.stringify(door));
  doorOfSig[sig] = owner.door;
});


// ---------------------------------------------------------------------------
// Nothing may still feed the legacy builder. This is the assertion the plan named:
// once it holds, `current` is rendered from the fragment list rather than from an
// accumulator, so it cannot drift out of step with the doors.
// ---------------------------------------------------------------------------
const stillLegacy = pushes.filter(function (p) { return p.legacy; });
assert.strictEqual(stillLegacy.length, 0,
  stillLegacy.length + ' push(es) still append to `html`, first: ' +
  (stillLegacy[0] ? stillLegacy[0].text.split('\n')[0] : ''));

// ---------------------------------------------------------------------------
// The whole ordered sequence, not just the cards that carry a signature. This is
// what pins the five pushes of the options card together, and the order itself:
// `current` renders the fragments in push order, so a reordered push is a
// reordered legacy page.
// ---------------------------------------------------------------------------
const S = 'scans', B = 'billing', R = 'remedies', L = 'solutions', A = 'account', E = 'learn';
const CT = 'chrome-top', CB = 'chrome-bottom';
// Task 11 (re-scoped) merges the "More savings ahead" and "Everything your
// membership unlocks" pushes into one `part("account", ...)` call under the shell,
// and keeps both originals for the shell-off page, so this sequence carries three
// consecutive account entries where the pre-branch source carried two.
const EXPECTED = [
  S, A, S, A, S, CT, null, null, null, null, null, null, null, S, S, S,
  B, B, B, B, B, B, B, S, S, S, S, S, S, A, S, R, R, R, R, R, S, L, R, R, R, L,
  A, A, A, B, null, null, null, null, null, A, null, A, S, S, S, E, E, E, A, null, A, A, CB
];
assert.strictEqual(EXPECTED.length, 65, 'the expected sequence must cover every push');
assert.deepStrictEqual(pushes.map(function (p) { return p.door; }), EXPECTED,
  'every push must name the door that owns its card, in source order');

// A card lands in exactly one door, and none vanished. 42 cards and a footer, from
// 65 pushes: 51 that fed `current` on every path, plus the 14 legacy-only arms of
// cards the hub already routes elsewhere, which have no door to land in. Two of the
// 51 are the shell-off arms of the membership card, mutually exclusive at run time
// with the merged one, so no client ever sees three.
const counts = {};
pushes.forEach(function (p) { counts[String(p.door)] = (counts[String(p.door)] || 0) + 1; });
assert.deepStrictEqual(counts,
  { scans: 17, billing: 8, remedies: 8, solutions: 2, account: 11, learn: 3,
    'chrome-top': 1, 'chrome-bottom': 1, 'null': 14 });

// ---------------------------------------------------------------------------
// The doors are actually rendered, and each renders its own filter. A door whose
// section rendered another door's fragments would satisfy every assertion above.
// ---------------------------------------------------------------------------
const SECTIONS = [
  ['scan-report', 'scans'], ['billing-detail', 'billing'], ['remedy-detail', 'remedies'],
  ['solutions-detail', 'solutions'], ['account-detail', 'account'], ['learn-detail', 'learn']
];
SECTIONS.forEach(function (row) {
  const panel = row[0], door = row[1];
  const tag = new RegExp('<section data-panel="' + panel + '"[^>]*>[^<]*').exec(page);
  assert.ok(tag, 'no section renders panel ' + panel);
  assert.ok(tag[0].indexOf('data-door="' + door + '"') !== -1,
    panel + ' must declare data-door="' + door + '"');
  assert.ok(tag[0].indexOf('partsFor("' + door + '")') !== -1,
    panel + ' must render partsFor("' + door + '"), got: ' + tag[0]);
});

// ---------------------------------------------------------------------------
// THE safety invariant: `current` and the door panels never hold the cards at the
// same time. This is not a style preference. 72 of the 88 static ids in the moved
// region are reached by a document-wide getElementById or querySelector elsewhere
// in the page, so a second copy is not inert: a door copy's controls would bind to
// the `current` copy, whichever the document reached first. And
// syncPortalHeaderCartCount() counts `#curatedOrderItems .curated-order-item`
// across the whole document, so with the basket in two panels the header cart
// badge would silently double, on the flag combination production runs today.
//
// Pinned as exact literals, on the legacy panel and on every door section. An
// earlier version of this file asserted only that each section mentioned its own
// partsFor(...) call, and the gate could be deleted from any of them with every
// suite still green.
// ---------------------------------------------------------------------------
const GATED_CURRENT =
  '<section data-panel="current"${_hub ? " hidden" : ""} data-door="scans">' +
  '${back}${html}${_doors ? "" : legacyCurrentHtml()}</section>';
assert.ok(page.indexOf(GATED_CURRENT) !== -1,
  'the legacy `current` section must render its fragments only when the doors do not: ' +
  'without the _doors gate every card is in the DOM twice');

SECTIONS.forEach(function (row) {
  const panel = row[0], door = row[1];
  const gated = '<section data-panel="' + panel + '" hidden data-door="' + door + '">' +
    '${back}${_doors ? partsFor("' + door + '") : ""}</section>';
  assert.ok(page.indexOf(gated) !== -1,
    panel + ' must render its fragments only under the _doors gate; without it the same ' +
    'cards are in both this section and `current`, duplicating their element ids');
});

// The gate itself must be the conjunction, not the shell flag alone. With the shell
// on and the hub off there are no door panels, so suppressing the fragments from
// `current` there would render a blank portal.
assert.ok(page.indexOf('const _doors = _hub && _shell;') !== -1,
  '_doors must be `_hub && _shell`: the door panels only exist under the hub');

// Page chrome renders outside the door panels, so it is on every door, not one, and
// in two containers rather than one: the practitioner co-brand band sits near the top
// of the page today, and a single trailing container would have sunk it below every
// card on every door.
const TOP_CHROME =
  '<div class="portal-chrome portal-chrome-top">${partsFor("chrome-top")}</div>';
const BOTTOM_CHROME =
  '<div class="portal-chrome portal-chrome-bottom">${partsFor("chrome-bottom")}</div>';
assert.ok(page.indexOf(TOP_CHROME) !== -1,
  'the top chrome container must render partsFor("chrome-top")');
assert.ok(page.indexOf(BOTTOM_CHROME) !== -1,
  'the bottom chrome container must render partsFor("chrome-bottom")');
// ...and they really are above and below the sections, not merely present.
const firstSection = page.indexOf('<section data-panel="current"');
const lastSection = page.lastIndexOf('<section data-panel="learn-detail"');
assert.ok(page.indexOf(TOP_CHROME) < firstSection,
  'the top chrome container must come before the panel sections');
assert.ok(page.indexOf(BOTTOM_CHROME) > lastSection,
  'the bottom chrome container must come after the panel sections');

// ---------------------------------------------------------------------------
// The legacy panel still shows everything. Emptying it would blank the portal on
// the unwrapped path, where it is the page's only content.
// ---------------------------------------------------------------------------
const currents = page.match(/<section data-panel="current"[^>]*>[^<]*/g) || [];
assert.strictEqual(currents.length, 2, 'expected both `current` renderings');
currents.forEach(function (c) {
  assert.ok(c.indexOf('legacyCurrentHtml()') !== -1,
    '`current` must render every fragment, got: ' + c);
});
// and on the unwrapped path it does so unconditionally: there are no door panels
// there to render the cards instead.
assert.ok(page.indexOf(
  '<section data-panel="current" data-door="scans">${html}${legacyCurrentHtml()}</section>') !== -1,
  'the unwrapped `current` must render the fragments with no flag in the way');

console.log('test_portal_current_split: ok (' + SIGS.length + ' signatures placed)');
