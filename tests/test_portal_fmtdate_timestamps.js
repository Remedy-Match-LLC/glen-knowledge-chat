// tests/test_portal_fmtdate_timestamps.js
// Run: node tests/test_portal_fmtdate_timestamps.js (run by tests/test_portal_fmtdate_timestamps.py)
//
// My Remedies showed "Last ordered 2026-09-10T20:00:00Z" because fmtDate only read a bare
// date and orders store a full timestamp (teaching, filming the portal tour, 4 Nov 2026).
// This runs the page's own fmtDate, sliced from client-portal.html.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

process.env.TZ = 'Pacific/Honolulu';  // pinned: the expected days below are Hawai'i days
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const i = src.indexOf('const fmtDate = ');
assert.ok(i !== -1, 'fmtDate not found');
const fmtDate = (0, eval)('(' + src.slice(i + 'const fmtDate = '.length, src.indexOf('\n};', i) + 2) + ')');

// A bare date keeps its calendar day.
assert.strictEqual(fmtDate('2026-10-08'), 'Oct 8, 2026');
// Order timestamps as the seed and orders._now() store them.
assert.strictEqual(fmtDate('2026-09-10T20:00:00Z'), 'Sep 10, 2026');
assert.strictEqual(fmtDate('2026-09-10T20:00:00.123456+00:00'), 'Sep 10, 2026');
// A late-UTC timestamp reads as the client's own day (Hawai'i is UTC-10).
assert.strictEqual(fmtDate('2026-11-04T05:00:00+00:00'), 'Nov 3, 2026');
// A six-digit fraction with a zone, as Python writes it, keeps the local day.
assert.strictEqual(fmtDate('2026-11-04T05:00:00.123456+00:00'), 'Nov 3, 2026');
// No zone: read as local time.
assert.strictEqual(fmtDate('2026-08-10T17:33:38.478076'), 'Aug 10, 2026');
// A date-led string the browser cannot parse falls back to its date part.
assert.strictEqual(fmtDate('2026-09-10 garbage'), 'Sep 10, 2026');
// Anything that does not start with a date is shown as it was, never re-read as a date.
assert.strictEqual(fmtDate('2'), '2');
assert.strictEqual(fmtDate('Order 2026-09-10'), 'Order 2026-09-10');
// Padding is ignored; a date-led string that is no date at all stays as it was.
assert.strictEqual(fmtDate(' 2026-10-08 '), 'Oct 8, 2026');
assert.strictEqual(fmtDate('2026-13-45 x'), '2026-13-45 x');
// The fraction trim protects stricter browsers; Node parses six digits itself, so pin it in source.
assert.ok(/s\.replace\(\/\(\\\.\\d\{3\}\)\\d\+\/, "\$1"\)/.test(src), 'six-digit fraction trim missing');
assert.strictEqual(fmtDate('Current'), 'Current');
assert.strictEqual(fmtDate(''), '');
assert.strictEqual(fmtDate(null), '');
// No raw timestamp ever reaches the page for an ISO order date.
for (const t of ['2026-09-10T20:00:00Z', '2026-11-03T20:00:00+00:00']) {
  assert.ok(!/T\d\d:/.test(fmtDate(t)), 'raw timestamp shown: ' + fmtDate(t));
}
console.log('ok test_portal_fmtdate_timestamps');
