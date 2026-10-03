// tests/test_portal_biofield_prereqs_card.js
// Run: node tests/test_portal_biofield_prereqs_card.js
//
// Glen 2026-10-02: a Biofield client finishes the fresh scan, intake and photo BEFORE
// paying, "otherwise she pays and 'waits'". The portal card must show each step's
// state and open the pay button only when all three are done.
//
// render() needs most of a DOM, so this cuts the card's own block out of the page
// and runs THAT code with stand-ins for part(), esc() and the view. It executes the
// shipped code; it does not grep it.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const start = page.indexOf('  // First Biofield test: the unchecked onboarding item lands here.');
const end = page.indexOf('  if (d.linked_practitioner_account) {', start);
assert.ok(start !== -1 && end > start, 'Biofield order card block not found');
const block = page.slice(start, end);

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function renderCard(prereqs, { biofieldDone = false } = {}) {
  const out = [];
  const part = (door, html) => out.push({ door, html });
  const v = {
    journey: { phases: [{ steps: [
      { key: 'voice', done: false, href: 'https://e4l.example/scan' },
      { key: 'intake', done: false, href: '#intake' },
      { key: 'photo', done: false, href: '#photo' },
      { key: 'biofield', done: biofieldDone, href: '#biofield-order' },
    ] }] },
    biofield_prereqs: prereqs,
  };
  new Function('v', 'part', 'esc', block)(v, part, esc);
  return out;
}

const button = (html) => {
  const m = html.match(/<button[^>]*id="biofieldOrderBtn"[^>]*>([^<]*)<\/button>/);
  return m && { disabled: /\sdisabled[\s>]/.test(m[0]), label: m[1] };
};

// Nothing done: three Needed rows, each with its link; pay button disabled.
{
  const [card] = renderCard({ photo: false, intake: false, scan: false, ready: false,
                              scan_window_days: 7, paid: false });
  assert.strictEqual(card.door, 'scans');
  assert.strictEqual((card.html.match(/data-done="0"/g) || []).length, 3);
  assert.ok(card.html.includes('href="https://e4l.example/scan"'), 'scan link uses the journey href');
  assert.ok(card.html.includes('href="#intake"') && card.html.includes('href="#photo"'));
  assert.ok(card.html.includes('within the last 7 days'));
  const b = button(card.html);
  assert.ok(b && b.disabled, 'pay button must be disabled before the three steps');
  assert.ok(!/after payment/i.test(card.html), 'card must not tell the client to pay first');
}

// Two of three done: still disabled; the done rows carry no action link.
{
  const [card] = renderCard({ photo: true, intake: true, scan: false, ready: false,
                              scan_window_days: 7, paid: false });
  assert.strictEqual((card.html.match(/data-done="1"/g) || []).length, 2);
  assert.ok(!card.html.includes('href="#intake"'), 'a done step offers no action');
  assert.ok(button(card.html).disabled);
}

// All three done: the pay button opens.
{
  const [card] = renderCard({ photo: true, intake: true, scan: true, ready: true,
                              scan_window_days: 7, paid: false });
  const b = button(card.html);
  assert.ok(b && !b.disabled, 'pay button opens once all three are done');
  assert.strictEqual(b.label, 'Pay for your Biofield Analysis');
}

// A missing readiness block fails closed: no open pay button.
{
  const [card] = renderCard(undefined);
  assert.ok(button(card.html).disabled, 'no readiness data must not open payment');
}

// Paid but not yet reported: no second payment is offered.
{
  const [card] = renderCard({ photo: true, intake: true, scan: true, ready: true,
                              scan_window_days: 7, paid: true });
  assert.strictEqual(button(card.html), null, 'a paid client is not offered payment again');
  assert.ok(card.html.includes('href="/biofield/ready"'));
}

// Report published: the card is not shown at all.
assert.strictEqual(renderCard({ ready: true }, { biofieldDone: true }).length, 0);

console.log('test_portal_biofield_prereqs_card: ok');
