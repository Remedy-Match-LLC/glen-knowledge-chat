// The condition checklist the practice owner authored, and the two surfaces
// that render it.
//
// There used to be exactly one surface: the onboarding tile's triage form in
// static/js/portal-onboarding.js, gated on status.history_conditions_done, so a
// client filled it in once during setup and never saw it again. A client who
// develops a new symptom in month three had no way to say so. The permanent
// "What you are working on" card in the Find Solutions door is the second
// surface, and it is re-runnable: it prefills with the client's current
// conditions and submitting reconciles the whole set.
//
// Two surfaces means two chances for the list to drift. So the list, the
// per-condition follow-up questions, and the payload/prefill helpers all live
// here, and both surfaces render from this one copy. renderConditionChecklist()
// returns byte-for-byte what portal-onboarding.js used to inline.
//
// Pure markup by design (no DOM reads, no fetch) so node can unit-test it,
// following the same dual-module pattern as portal-shell.js. All labels/text
// here are fixed copy (no server-provided strings), so nothing needs escaping.

// Eye and vision issues. `other` is the free-text escape hatch and is the only
// entry with no condition program behind it.
var EYE_CONDITIONS = [
  {value: 'glaucoma', label: 'Glaucoma'},
  {value: 'cataract', label: 'Cataracts'},
  {value: 'macular', label: 'Macular degeneration'},
  {value: 'dry-eye', label: 'Dry eye'},
  {value: 'retinitis-pigmentosa', label: 'Retinitis pigmentosa'},
  {value: 'diabetic-retinopathy', label: 'Diabetic retinopathy'},
  {value: 'vision-improvement', label: 'Reduced vision or vision you want to improve'},
  {value: 'other', label: 'Other'}
];

// Whole-body symptoms. Each resolves to exactly one program (see
// condition_triage._SINGLE_PROGRAM), so none of them carries a follow-up.
var SYSTEMIC_SYMPTOMS = [
  {value: 'symptom-fatigue', label: 'Fatigue or low energy'},
  {value: 'symptom-brain-fog', label: 'Brain fog or poor focus'},
  {value: 'symptom-stress', label: 'Stress or anxious tension'},
  {value: 'symptom-sleep', label: 'Trouble sleeping'},
  {value: 'symptom-headache', label: 'Headache or migraine'},
  {value: 'symptom-digestion', label: 'Digestive discomfort or bloating'},
  {value: 'symptom-constipation', label: 'Constipation or irregularity'},
  {value: 'symptom-immune', label: 'Frequent illness or slow recovery'},
  {value: 'symptom-skin', label: 'Common skin concerns'},
  {value: 'symptom-blood-sugar', label: 'Blood sugar swings or cravings'}
];

function conditionValues() {
  return EYE_CONDITIONS.concat(SYSTEMIC_SYMPTOMS).map(function (c) { return c.value; });
}

function conditionLabel(value) {
  var all = EYE_CONDITIONS.concat(SYSTEMIC_SYMPTOMS);
  for (var i = 0; i < all.length; i++) {
    if (all[i].value === value) return all[i].label;
  }
  return value;
}

function _conditionChoice(item) {
  return '<label class="ob-condition-choice"><input type="checkbox" name="conditions" value="' +
    item.value + '"> ' + item.label + '</label>';
}

function _choiceList(items) {
  return items.map(_conditionChoice).join('');
}

function _detailCheck(name, label) {
  return '<label class="ob-triage-check"><input type="checkbox" name="' +
    name + '"> ' + label + '</label>';
}

function _glaucomaFollowup() {
  return '<div class="ob-condition-detail" data-condition-detail="glaucoma" hidden>' +
    '<p class="ob-followup-title">A few details about your glaucoma</p>' +
    '<label class="ob-triage-field">What type or pressure pattern were you told you have?' +
      '<select name="category"><option value="">Not sure</option><option value="elevated">Elevated-pressure glaucoma</option><option value="normal">Normal-tension glaucoma</option></select></label>' +
    '<div class="ob-triage-row">' +
      '<label class="ob-triage-field">Right eye pressure (OD)<input type="number" step="0.1" min="0" max="60" name="iop_od" placeholder="e.g. 18"></label>' +
      '<label class="ob-triage-field">Left eye pressure (OS)<input type="number" step="0.1" min="0" max="60" name="iop_os" placeholder="e.g. 18"></label>' +
    '</div>' +
    _detailCheck('on_meds', 'I use eye-pressure-lowering medication') +
    '<label class="ob-triage-field ob-inline-field">If yes, how many medications?<input type="number" min="0" max="20" name="med_count"></label>' +
    _detailCheck('field_loss', 'I have peripheral vision loss') +
  '</div>';
}

function _cataractFollowup() {
  return '<div class="ob-condition-detail" data-condition-detail="cataract" hidden>' +
    '<p class="ob-followup-title">A few details about your cataracts</p>' +
    '<label class="ob-triage-field">What type were you told you have?' +
      '<select name="cataract_type"><option value="">Not sure</option><option value="senile">Age-related / nuclear</option><option value="psc">Posterior subcapsular (PSC)</option></select></label>' +
    '<label class="ob-triage-field ob-inline-field">Your age<input type="number" min="0" max="120" name="age"></label>' +
    _detailCheck('steroids', 'Current or past steroid use') +
    _detailCheck('diabetes', 'Diabetes') +
    _detailCheck('inflammation', 'Chronic inflammation') +
    _detailCheck('radiation', 'Radiation exposure or treatment') +
    _detailCheck('atopy', 'Allergies, asthma, or eczema') +
    _detailCheck('yellow_vision', 'Vision seems more yellow or less blue') +
  '</div>';
}

function _macularFollowup() {
  return '<div class="ob-condition-detail" data-condition-detail="macular" hidden>' +
    '<p class="ob-followup-title">A few details about your macular degeneration</p>' +
    '<label class="ob-triage-field">What type were you told you have?' +
      '<select name="amd_type"><option value="">Not sure</option><option value="dry">Dry</option><option value="wet">Wet</option></select></label>' +
    _detailCheck('injections', 'I receive eye injections for it') +
    _detailCheck('distortion', 'Straight lines look bent, wavy, or distorted') +
  '</div>';
}

function _dryEyeFollowup() {
  return '<div class="ob-condition-detail" data-condition-detail="dry-eye" hidden>' +
    '<p class="ob-followup-title">A few details about your dry eye</p>' +
    '<label class="ob-triage-field">Do your eyes make enough tears?' +
      '<select name="not_enough_tears"><option value="">Not sure</option><option value="false">Yes</option><option value="true">No</option></select></label>' +
    _detailCheck('sjogrens', 'I also have dry mouth or vaginal dryness') +
    _detailCheck('severe', 'My dry eye is severe') +
  '</div>';
}

// The checklist body: both condition lists plus every follow-up block. The
// onboarding tile wraps this in its .ob-history-section; the Find Solutions
// card wraps it in its own form. Identical markup either way, deliberately, so
// one set of CSS rules and one set of prefill/payload helpers serve both.
function renderConditionChecklist() {
  return '' +
    '<p class="ob-triage-intro">Do you have any of these eye or vision issues? Check all that apply.</p>' +
    '<div class="ob-condition-list">' + _choiceList(EYE_CONDITIONS) + '</div>' +
    _glaucomaFollowup() + _cataractFollowup() +
    _macularFollowup() + _dryEyeFollowup() +
    '<div class="ob-condition-detail" data-condition-detail="other" hidden>' +
      '<p class="ob-followup-title">Tell us what else is going on</p>' +
      '<label class="ob-triage-field">Other eye or vision issue' +
        '<textarea name="other_condition" rows="3"></textarea></label>' +
    '</div>' +
    '<p class="ob-triage-intro" style="margin-top:1rem">Do you commonly experience any of these whole-body symptoms? Check all that apply.</p>' +
    '<div class="ob-condition-list ob-systemic-symptom-list">' +
      _choiceList(SYSTEMIC_SYMPTOMS) + '</div>';
}

// The permanent Find Solutions card. Unlike the onboarding form it is never
// gated: it is the standing answer to "something new is going on and I want
// remedies for it". Empty of prefill at render time; the wiring in
// client-portal.html fills it from the client's stored conditions through
// applyConditionPrefill below, so the checkboxes always show what the server
// currently holds rather than a snapshot baked into markup.
function renderWorkingOnCard() {
  return '<div class="card" id="workingOnCard">' +
    '<h2>What you are working on</h2>' +
    '<p class="muted small">Check anything you are working on now, and uncheck anything that has cleared up. ' +
      'We match remedies to what is checked here, so unchecking something also takes its remedies off your list.</p>' +
    '<form class="wo-form">' +
      renderConditionChecklist() +
      '<button type="submit" class="btn full wo-submit">Update my matches</button>' +
      '<p class="wo-msg small" aria-live="polite"></p>' +
    '</form>' +
    '<div class="wo-matches"></div>' +
  '</div>';
}

// Show or hide one condition's follow-up block, scoped to the form the
// checkbox lives in. Both surfaces call this from their own change listener.
function syncConditionDetail(form, value, checked) {
  if (!form) return;
  var detail = form.querySelector('[data-condition-detail="' + value + '"]');
  if (detail) detail.hidden = !checked;
}

// Prefill one checklist form from build_status()'s history_prefill.conditions
// ([{condition, answers}]). Returns the values it checked, so a caller can tell
// what the server currently holds without re-reading the DOM.
function applyConditionPrefill(form, conditions) {
  var selected = [];
  var answers = {};
  (conditions || []).forEach(function (item) {
    if (!item || !item.condition) return;
    selected.push(item.condition);
    Object.keys(item.answers || {}).forEach(function (key) {
      answers[key] = item.answers[key];
    });
  });
  if (!form) return selected;
  Array.prototype.forEach.call(form.querySelectorAll('input[name="conditions"]'), function (box) {
    box.checked = selected.indexOf(box.value) !== -1;
    syncConditionDetail(form, box.value, box.checked);
  });
  Array.prototype.forEach.call(
    form.querySelectorAll('.ob-condition-detail input, .ob-condition-detail select, .ob-condition-detail textarea'),
    function (field) {
      if (!field.name) return;
      var value = answers[field.name];
      if (value === undefined || value === null) return;
      if (field.type === 'checkbox') field.checked = !!value;
      else field.value = value;
    });
  return selected;
}

function _num(v) {
  if (v === '' || v == null) return undefined;
  var n = parseFloat(v);
  return isNaN(n) ? undefined : n;
}

// One condition's submit payload, read live out of its follow-up block.
function conditionPayload(form, condition) {
  var detail = form.querySelector('[data-condition-detail="' + condition + '"]');
  var payload = {condition: condition};
  if (!detail) return payload;
  Array.prototype.forEach.call(detail.querySelectorAll('input,select,textarea'), function (field) {
    var value;
    if (field.type === 'checkbox') {
      value = field.checked;
    } else if (field.type === 'number') {
      value = _num(field.value);
    } else if (field.name === 'not_enough_tears') {
      value = field.value === '' ? undefined : field.value === 'true';
    } else {
      value = field.value || undefined;
    }
    if (value !== undefined) payload[field.name] = value;
  });
  return payload;
}

var _EXPORTS = {
  EYE_CONDITIONS: EYE_CONDITIONS,
  SYSTEMIC_SYMPTOMS: SYSTEMIC_SYMPTOMS,
  conditionValues: conditionValues,
  conditionLabel: conditionLabel,
  renderConditionChecklist: renderConditionChecklist,
  renderWorkingOnCard: renderWorkingOnCard,
  syncConditionDetail: syncConditionDetail,
  applyConditionPrefill: applyConditionPrefill,
  conditionPayload: conditionPayload
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = _EXPORTS;
}
if (typeof window !== 'undefined') {
  window.PortalConditions = _EXPORTS;
}
