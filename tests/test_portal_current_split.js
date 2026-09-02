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
// helper above its push -- `let s = ...; part("scans", s);` -- is found in that
// slice, which is why the window and not just the statement is captured.
// ---------------------------------------------------------------------------
const pushes = [];
const re = /(?:(?<![A-Za-z0-9_$.])part\(\s*(null|"[a-z]+")\s*,)|(?:(?<![A-Za-z0-9_$.])html\s*\+=)/g;
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
assert.strictEqual(pushes.length, 64,
  'expected 64 pushes into the page body, parsed ' + pushes.length);

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
  ['account', '<h2>More savings ahead</h2>'],
  ['account', '<h2>Everything your membership unlocks</h2>'],  // membership upsell
  ['account', '<h2>Free Product Review</h2>'],
  ['account', 'class="card notifpref quiet"'],                 // notification preference
  ['account', '<h2 style="font-size:1rem">Sharing</h2>'],
  ['account', '<h2 style="font-size:1rem">Family notifications</h2>'],
  ['account', 'id="scanPrefsCard"']                            // Your preferences
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

console.log('test_portal_current_split: ok (' + SIGS.length + ' signatures placed)');
