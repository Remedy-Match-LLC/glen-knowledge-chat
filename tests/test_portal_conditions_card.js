// The permanent "What you are working on" checklist in the Find Solutions door,
// and the one thing that makes it safe to have two copies of the same checklist
// on the page: they render from one source, and they cannot reach each other.
//
// Everything below executes the real modules. The DOM behaviour (prefill,
// submit, reconcile, matches) is verified by driving the rendered page, not
// from here.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const conditions = require(path.join(ROOT, 'static/js/portal-conditions.js'));
const onboarding = require(path.join(ROOT, 'static/js/portal-onboarding.js'));
const page = fs.readFileSync(path.join(ROOT, 'static/client-portal.html'), 'utf8');

const card = conditions.renderWorkingOnCard();
const checklist = conditions.renderConditionChecklist();
const values = conditions.conditionValues();

// ---------------------------------------------------------------------------
// One authored list. The reason this module exists.
// ---------------------------------------------------------------------------
assert.strictEqual(values.length, 18,
  'expected 18 authored checklist values, got ' + values.length);
assert.strictEqual(new Set(values).size, values.length, 'duplicate condition value');
values.forEach(function (v) {
  assert.ok(card.indexOf('value="' + v + '"') !== -1,
    'the Find Solutions card is missing condition ' + v);
});

// The onboarding tile renders the SAME checklist body, character for character.
// Not "both contain glaucoma": the whole block, so a label reworded in one place
// cannot silently differ in the other.
const tile = onboarding.renderOnboarding({
  phases: [{key: 'match', title: 'Match remedies',
            steps: [{key: 'history', label: 'Match Remedies', done: false, href: '#recs'}]}],
  history_conditions_done: false
});
assert.ok(tile.indexOf(checklist) !== -1,
  'the onboarding tile no longer renders the shared checklist body verbatim');
assert.ok(card.indexOf(checklist) !== -1,
  'the Find Solutions card no longer renders the shared checklist body verbatim');

// ---------------------------------------------------------------------------
// The two copies must not collide when both are on screen during setup.
// ---------------------------------------------------------------------------

// 1. No shared element id. The card carries exactly one id, and the tile none
//    that matches it.
const idsIn = (html) => (html.match(/\sid="[^"]+"/g) || []).map(s => s.slice(5, -1));
const cardIds = idsIn(card);
const tileIds = idsIn(tile);
assert.deepStrictEqual(cardIds, ['workingOnCard'],
  'unexpected ids in the Find Solutions card: ' + JSON.stringify(cardIds));
cardIds.forEach(function (id) {
  assert.ok(tileIds.indexOf(id) === -1, 'id ' + id + ' is emitted by both checklists');
});

// 2. The card's form must NOT carry the class the onboarding tile's delegated
//    submit handler matches on, or one submit would fire both handlers and the
//    same condition would be seeded twice.
const OB_SUBMIT_SELECTOR = ".closest('.ob-triage-form')";
const obSource = fs.readFileSync(path.join(ROOT, 'static/js/portal-onboarding.js'), 'utf8');
assert.ok(obSource.indexOf(OB_SUBMIT_SELECTOR) !== -1,
  'the onboarding submit handler no longer matches on .ob-triage-form; re-check this guard');
assert.ok(card.indexOf('ob-triage-form') === -1,
  'the Find Solutions form carries .ob-triage-form and would fire the onboarding submit handler too');
assert.ok(card.indexOf('class="wo-form"') !== -1, 'the Find Solutions form lost its own class');

// 3. The onboarding handlers are bound on #portal-onboarding-mount, which is a
//    sibling of #app; the card lives inside #app. Neither listener can see the
//    other's form. Pin both halves.
assert.ok(obSource.indexOf("mount.addEventListener('submit'") !== -1,
  'the onboarding submit listener is no longer scoped to its mount');
assert.ok(page.indexOf('<div id="portal-onboarding-mount"></div>') !== -1,
  'the onboarding mount is no longer a standalone sibling node');
assert.ok(page.indexOf('const card = document.getElementById("workingOnCard");') !== -1,
  'the Find Solutions wiring is no longer scoped to its own card');

// ---------------------------------------------------------------------------
// The card shows its matches through the SAME renderer the My Recommendations
// card uses, and must not duplicate that card's id.
// ---------------------------------------------------------------------------
assert.ok(page.indexOf('function renderRecommendationSections(sections){') !== -1,
  'renderRecommendationSections was removed; the card would need a second renderer');
assert.ok(page.indexOf('matches.innerHTML = renderRecommendationSections(conditionSections);') !== -1,
  'the Find Solutions card no longer renders its matches through the shared renderer');
assert.ok(card.indexOf('recsCard') === -1, 'the card must not carry the My Recommendations id');

// ---------------------------------------------------------------------------
// Copy rules: no em dash, no ALL CAPS words, and "client" never "patient".
// ---------------------------------------------------------------------------
const text = card.replace(/<[^>]*>/g, ' ');
assert.ok(text.indexOf('—') === -1, 'em dash in client-facing copy');
assert.ok(!/\bpatient\b/i.test(text), '"patient" in client-facing copy');
assert.ok(!/\b[A-Z]{3,}\b/.test(text.replace(/\bPSC\b|\bOD\b|\bOS\b/g, '')),
  'ALL CAPS word in client-facing copy');

console.log('test_portal_conditions_card: ok (' + values.length + ' conditions, one shared checklist)');
