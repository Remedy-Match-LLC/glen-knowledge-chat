// tests/test_portal_health_suggestion_card.js
// Run: node tests/test_portal_health_suggestion_card.js
//
// The portal "Suggested updates" card must show a table-row suggestion readably
// (column labels, not raw JSON), and an Edit of a table row must send an object.
// This cuts the real helper functions out of the page and runs them.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

function fn(name) {
  const start = page.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found');
  // A top-level function ends at the first "\n}\n" after it.
  const end = page.indexOf('\n}\n', start);
  assert.ok(end > start, name + ' end not found');
  return page.slice(start, end + 2);
}

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const src = [
  fn('findHealthField'), fn('healthFieldLabel'), fn('healthSuggestionLabel'), fn('healthRowText'),
  fn('healthSuggestionValueText'), fn('healthSuggestionItemHtml'),
].join('\n');

const block = {
  sections: [{ title: 'Personal Health History', fields: [
    { id: 'medications', label: 'Prescription medications you are currently taking',
      type: 'table', value: [],
      columns: [{ id: 'medication', label: 'Medication' }, { id: 'reason', label: 'Reason' }] },
    { id: 'alcohol', label: 'Alcohol: how much, what type, and how often?', type: 'textarea',
      value: '' },
    { id: 'family_history', label: 'Family health history', type: 'table', value: [],
      columns: [{ id: 'relative', label: 'Relative / side of family' },
        { id: 'condition', label: 'Condition' }, { id: 'age_onset', label: 'Age at onset' }] },
    { id: 'supplements', label: 'Supplements you take now', type: 'table', value: [],
      columns: [{ id: 'brand', label: 'Brand name' }, { id: 'name', label: 'Supplement name' },
        { id: 'reason', label: 'Reason' }, { id: 'need', label: 'Need (1-10)' }] },
    { id: 'sleep', label: 'Do you have trouble falling asleep, staying asleep, or wake frequently?',
      type: 'textarea', value: '' },
  ] }],
};

const run = new Function('esc', '_healthProfileBlock',
  src + '\nreturn {healthSuggestionItemHtml, healthSuggestionValueText, healthSuggestionLabel};');
const api = run(esc, block);

// A table row reads as labels and values, never as JSON.
const row = { id: 7, source: 'chat', field_id: 'medications',
  suggested_value: JSON.stringify({ medication: 'Warfarin', reason: 'blood thinner' }),
  rationale: '"I take warfarin as a blood thinner"' };
assert.strictEqual(api.healthSuggestionValueText(row), 'Medication: Warfarin; Reason: blood thinner');
const html = api.healthSuggestionItemHtml(row);
assert.ok(html.includes('Suggested: <strong>Medication: Warfarin; Reason: blood thinner</strong>'), html);
const visible = html.replace(/data-raw-value="[^"]*"/, '');
assert.ok(!visible.includes('{&quot;'), 'raw JSON leaked into the visible text');
assert.ok(html.includes('Update Prescription medications you are currently taking?'), html);
assert.ok(html.includes('data-raw-value="{&quot;medication&quot;:&quot;Warfarin&quot;'), html);

// A text suggestion is shown as stored.
const text = { id: 8, source: 'chat', field_id: 'alcohol',
  suggested_value: 'Two glasses of wine most nights', rationale: '"two glasses of wine"' };
assert.strictEqual(api.healthSuggestionValueText(text), 'Two glasses of wine most nights');

// Edit of a table suggestion builds an object from per-column inputs.
assert.ok(page.includes('payload = {action: "edit", value: row};'), 'table edit must send an object');
assert.ok(page.includes('class="health-sugg-edit-col" data-col='), 'table edit needs column inputs');

// New intake fields (Glen, 20 Oct 2026): family history and supplements read as
// column labels, and a question-shaped form label gets a short name on the card.
const fam = { id: 9, source: 'chat', field_id: 'family_history',
  suggested_value: JSON.stringify({ relative: 'Mother', condition: 'Breast cancer' }),
  rationale: '"My mother had breast cancer"' };
assert.strictEqual(api.healthSuggestionValueText(fam),
  'Relative / side of family: Mother; Condition: Breast cancer');
assert.ok(api.healthSuggestionItemHtml(fam).includes('Update Family health history?'));
const sup = { id: 10, source: 'chat', field_id: 'supplements',
  suggested_value: JSON.stringify({ name: 'Magnesium glycinate' }), rationale: '"x"' };
assert.strictEqual(api.healthSuggestionValueText(sup), 'Supplement name: Magnesium glycinate');
const slp = { id: 11, source: 'chat', field_id: 'sleep',
  suggested_value: 'I sleep badly, waking at 3 every night', rationale: '"x"' };
const slpHtml = api.healthSuggestionItemHtml(slp);
assert.ok(slpHtml.includes('Update Sleep?'), slpHtml);
assert.ok(!slpHtml.includes('??'), slpHtml);
// Every field the chat can suggest has a card name that is not a question.
for (const [fid, name] of Object.entries({ health_concerns: 'Health concerns',
  other_symptoms: 'Other symptoms', sleep: 'Sleep', dental: 'Dental history',
  vaccinations: 'Vaccinations', diet: 'Diet', alcohol: 'Alcohol' })) {
  assert.strictEqual(api.healthSuggestionLabel(fid), name, fid);
}
assert.strictEqual(api.healthSuggestionLabel('family_history'), 'Family health history');

console.log('ok - portal health suggestion card');
