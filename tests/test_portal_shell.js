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
assert.deepStrictEqual(panelsForDoor('billing'), ['orders', 'billing-detail']);
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
  (page.match(/data-panel="[a-z][a-z-]*"/g) || []).map(s => s.slice(12, -1))
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

// Task 8 (portal-shell-ia): the top-of-page chat composer. A single-line entry
// point, not a second transcript, so it must carry the input the page's
// sendChatMessage() reads by id, and must not carry a message-bubble container.
const { renderComposer } = require('../static/js/portal-shell.js');
const composer = renderComposer();
assert.ok(composer.indexOf('id="chatInput"') !== -1,
  'renderComposer must render the chat input sendChatMessage() reads by id');
assert.strictEqual((composer.match(/id="chatInput"/g) || []).length, 1,
  'renderComposer must render exactly one chat input');
assert.ok(composer.indexOf('Ask me anything, or tell me what you need') !== -1,
  'renderComposer must carry the placeholder copy the brief specifies');
assert.strictEqual(composer.indexOf('chatMsgs'), -1,
  'renderComposer must not render a message-bubble container, it is an entry point only');
assert.strictEqual(composer.indexOf('chat-msgs'), -1,
  'renderComposer must not render a message-bubble container, it is an entry point only');
assert.ok(!/—|--/.test(composer), 'composer copy must not contain an em dash');
assert.ok(!/[A-Z]{4,}/.test(composer), 'composer copy must not be in ALL CAPS');
assert.ok(!/patient/i.test(composer), 'composer copy says client, never patient');

// Task 9 (portal-shell-ia): the chat routes to a door instead of describing it.
const { routeIntent } = require('../static/js/portal-shell.js');

// the four jobs the portal exists to serve
assert.strictEqual(routeIntent('where is my invoice'), 'billing');
assert.strictEqual(routeIntent("What do I owe?"), 'billing');
assert.strictEqual(routeIntent('I want to see my biofield report'), 'scans');
assert.strictEqual(routeIntent('can I see my scan'), 'scans');
assert.strictEqual(routeIntent('reorder my remedies'), 'remedies');
assert.strictEqual(routeIntent('what helps with dry eyes'), 'solutions');
assert.strictEqual(routeIntent('how do I finish setting up'), 'home');

// every destination it can return is a real door
['where is my invoice', 'show my scan', 'reorder', 'what helps with floaters']
  .forEach(function (q) {
    const d = routeIntent(q);
    assert.ok(DOORS.some(function (x) { return x.key === d; }),
      q + ' routed to ' + d + ', which is not a door');
  });

// An unknown intent must return null, never a guess. A wrong destination is worse
// than no destination: the client lands somewhere irrelevant and stops trusting it.
assert.strictEqual(routeIntent('is Dr. Glen in Hawaii'), null);
assert.strictEqual(routeIntent(''), null);
assert.strictEqual(routeIntent(null), null);
assert.strictEqual(routeIntent('thank you'), null);

// Fix round 1: bare dictionary words over-matched ordinary client sentences that
// happen to share vocabulary with a door. Each pair below was watched to fail
// against the pre-fix pattern before the pattern was tightened.

// billing: "pay", "bill" and "charge" are ordinary words. This practice works in
// energetic medicine, where a client describing a low energy charge, or a diet
// habit, or someone named Bill, must never be silently sent to an invoice screen.
assert.strictEqual(routeIntent('my energy charge feels low'), null);
assert.strictEqual(routeIntent('I pay attention to my diet'), null);
assert.strictEqual(routeIntent('Bill said the drops helped him'), null);
assert.strictEqual(routeIntent('I was charged again for the same order'), 'billing');

// scans: bare "report" also means reporting a problem, nothing to do with a scan.
assert.strictEqual(routeIntent('I want to report a problem with my order'), null);
assert.strictEqual(routeIntent('here is my report from last visit'), 'scans');

// remedies: bare "dose" also appears in idiom and in a symptom description. A
// client describing a new symptom should land on Find Solutions, not My
// Remedies, because they are asking what might help, not naming a medication.
assert.strictEqual(routeIntent('I have a new symptom, a sharp dose of pain in my knee'), 'solutions');

// solutions: "help me with" (a password) must never fire the "help with" trigger,
// and bare "condition" must not fire on a garden, a car, or an order.
assert.strictEqual(routeIntent('can you help me with my password'), 'account');
assert.strictEqual(routeIntent("what's the condition of my garden"), null);

// learn: bare "course" and "class" over-match the affirmation "of course" and an
// unrelated "cooking class".
assert.strictEqual(routeIntent('yes, of course!'), null);
assert.strictEqual(routeIntent('when is the next class'), 'learn');

// account: bare "address" and "photo" over-match "address my concern" (a verb)
// and "photo of my dog".
assert.strictEqual(routeIntent('please address my concern about the delay'), null);
assert.strictEqual(routeIntent('I love that photo of my dog'), null);
assert.strictEqual(routeIntent('can you update my address'), 'account');

// home: bare "set up" over-matches scheduling a call and a client's home
// equipment, neither of which is onboarding.
assert.strictEqual(routeIntent('set up a call with Dr Glen'), null);
assert.strictEqual(routeIntent('my setup at home is noisy'), null);

// Fix round 2: a reviewer probed with sentences neither prior pass had
// imagined and found the same class of bug in three more bare words, plus a
// redundant alternative. Each pair below was watched to fail against the
// pre-fix pattern before the pattern was tightened.

// solutions: bare "recommend"/"suggest" also fire when a client recommends a
// friend, a book, or a practitioner to someone else, not a request for a
// remedy. "suggest" was dropped outright, it has no unambiguous form worth
// keeping.
assert.strictEqual(routeIntent('I would like to recommend a friend to this practice'), null);
assert.strictEqual(routeIntent('can you recommend a good book on energy medicine'), null);
assert.strictEqual(routeIntent('my sister asked me to recommend a practitioner'), null);
assert.strictEqual(routeIntent('I suggest we talk on the phone instead'), null);
assert.strictEqual(routeIntent('what do you recommend for my dry eyes'), 'solutions');
assert.strictEqual(routeIntent('what do you recommend'), 'solutions');
assert.strictEqual(routeIntent('can you recommend something for floaters'), 'solutions');

// remedies: bare "protocol" also fires on a scheduling or process question,
// nothing to do with a remedy.
assert.strictEqual(routeIntent('what is the protocol for canceling a session'), null);
assert.strictEqual(routeIntent('what is the protocol if I need to reach Dr Glen after hours'), null);
assert.strictEqual(routeIntent('what is my protocol'), 'remedies');
assert.strictEqual(routeIntent('the protocol for taking the drops'), 'remedies');
assert.strictEqual(routeIntent('what is the dosing protocol'), 'remedies');

// account: bare "profile" also fires on a description of someone's public
// standing, nothing to do with the client's own account.
assert.strictEqual(routeIntent('she has a great profile as a healer'), null);
assert.strictEqual(routeIntent('he has a low profile but great results'), null);
assert.strictEqual(routeIntent('update my profile photo'), 'account');
assert.strictEqual(routeIntent('change my profile'), 'account');

// billing: "my card" was redundant with the charge pattern and also fired on
// losing a card at the clinic, nothing to do with billing.
assert.strictEqual(routeIntent('I lost my card at the clinic'), null);
assert.strictEqual(routeIntent('my card was charged twice'), 'billing');
assert.strictEqual(routeIntent('I was charged for something I did not order'), 'billing');

console.log('test_portal_shell: ok');
