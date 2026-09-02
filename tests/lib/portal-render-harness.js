// tests/lib/portal-render-harness.js
//
// Executes static/client-portal.html's render() body without a DOM.
//
// render() cannot be called: it needs a document, a token, and two dozen sibling
// builders. So its body is sliced out of the page source, from `let html = ` to
// `app.innerHTML = html;`, and executed as a function of the flags and payloads
// that decide what it emits. Helpers defined OUTSIDE that slice are replaced by
// stubs returning a marker string that names them; `esc`, `money` and `ICON_LOCK`
// are taken from the real source, because they shape output.
//
// Callers that compare two versions of the page (the flag-off snapshot) get their
// isolation from this: identical stubs on both sides mean only the sliced code can
// differ. Callers that inspect one version (the door back control) get a real
// assembled page rather than a source grep, which is the difference between
// pinning what renders and pinning what is written down.
//
// Not a test file: tests/lib is outside the tests/*.js runner glob on purpose.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const PAGE = path.join(__dirname, '..', '..', 'static', 'client-portal.html');

// Each stub is a builder with its own test. A name added here is coverage given
// up, so the list is meant to stay short.
const STUBS = [
  'escGreeting', 'stripSalutation', 'renderEyeVisionReport', 'buildCalendarHtml',
  'buildAppointmentProposalHtml', 'buildMembershipSummaryHtml', 'buildScanHistoryHtml',
  'buildOrdersHtml', 'buildClinicalRecordHtml', 'buildCartHtml',
  'buildShopHtml', 'buildPhotoHtml', 'buildOasisHtml', 'buildRemediesHtml',
  'buildOffersEmptyHtml', 'supportProgramCardHtml'
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

// A whole top-level function, by name. Used for the two back-control builders,
// whose markup is what the door back-control test counts, so stubbing them would
// erase the thing under test.
function fnSource(src, name, optional) {
  const at = src.indexOf('\nfunction ' + name + '(');
  // `optional` covers backToHome(), which this branch introduces: the flag-off
  // snapshot is generated from a source that predates it, and the slice only
  // calls it under the shell, which that generation never turns on.
  if (at === -1 && optional) return '';
  assert.ok(at !== -1, 'function ' + name + '() not found');
  const end = src.indexOf('\n}', at);
  assert.ok(end !== -1, 'unterminated function ' + name + '()');
  return src.slice(at, end + 2);
}

function line(src, marker) {
  const i = src.indexOf(marker);
  assert.ok(i !== -1, 'source line not found: ' + marker);
  return src.slice(i, src.indexOf('\n', i));
}

// Returns render(d, v, _hub, _shell, _doors, ...) -> the html handed to
// app.innerHTML. `backToHub`/`backToHome` are NOT stubbed: their markup is what
// the door back-control test counts.
function buildRenderer(src) {
  const stubs = STUBS.map(function (n) {
    return '  var ' + n + ' = function(){ return "[[' + n + ']]"; };';
  }).join('\n');
  const code = [
    line(src, 'const esc = '),
    line(src, 'const money = '),
    line(src, 'const ICON_LOCK = '),
    fnSource(src, 'backToHub'),
    fnSource(src, 'backToHome', true),
    '(function(d, v, _hub, _shell, _doors, seg, token, onboardingMount, hubHtml, first, badges, recSections, _ppTimer, location, window){',
    stubs,
    slice(src),
    '  return html;',
    '})'
  ].join('\n');
  return (0, eval)(code); // eslint-disable-line no-eval
}

// Default arguments, so a caller only names what it varies.
function render(src, opts) {
  opts = opts || {};
  const fn = buildRenderer(src);
  return fn(
    opts.d || {}, opts.v || {},
    !!opts.hub, !!opts.shell, !!opts.doors,
    'SEG', 'TOK', null, '[[hubHtml]]',
    'Mary', [], [], '',
    { origin: 'https://example.test', href: 'https://example.test/portal/SEG' },
    opts.window || {}
  );
}

// The page's panel sections, in DOM order, parsed from rendered output. Every
// <section> in static/client-portal.html is a panel section and none nest, which
// tests/test_portal_hash_routes.js and this file both rely on.
function sections(html) {
  const out = [];
  const re = /<section ([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const close = html.indexOf('</section>', m.index);
    assert.ok(close !== -1, 'unterminated <section> in rendered output');
    const panel = /data-panel="([a-z-]+)"/.exec(m[1]);
    assert.ok(panel, 'a <section> carries no data-panel: ' + m[1]);
    out.push({ panel: panel[1], attrs: m[1], body: html.slice(m.index + m[0].length, close) });
  }
  assert.ok(out.length > 3, 'implausibly few panel sections parsed: ' + out.length);
  return out;
}

module.exports = {
  PAGE: PAGE, STUBS: STUBS,
  read: function () { return fs.readFileSync(PAGE, 'utf8'); },
  slice: slice, buildRenderer: buildRenderer, render: render, sections: sections
};
