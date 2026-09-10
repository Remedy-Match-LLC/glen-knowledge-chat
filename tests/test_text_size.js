/* Unit tests for static/text-size.js, the per-viewer text magnification control.
 *
 * Plain node + assert, at tests/*.js, because that is the only front-end shape
 * ci/run-tests.sh actually runs. (tests/theme/*.test.cjs uses node:test in a
 * subfolder and CI never picks it up. Not fixed here, but worth knowing.)
 *
 * _normalize and _step are pure. No localStorage in the shim, so the guarded
 * auto-run at the bottom of the module stays off.
 */
const assert = require('node:assert');

global.window = {};
global.document = { documentElement: {}, addEventListener() {} };
require('../static/text-size.js');
const T = global.window.RMTextSize;

// The default must be 100, or every existing page silently changes size.
assert.strictEqual(T.DEFAULT, 100, 'the default must be 100 percent');
assert.strictEqual(T.STEPS[0], 100, 'the smallest step is the default');
assert.strictEqual(T._normalize(null), 100, 'no stored value means the default');
assert.strictEqual(T._normalize(''), 100, 'an empty stored value means the default');

// A stored value is snapped to a real rung.
assert.strictEqual(T._normalize('115'), 115);
assert.strictEqual(T._normalize(116), 115, 'snaps to the nearest step');
assert.strictEqual(T._normalize('128'), 130, 'snaps upward when nearer');

// Nothing can leave the page at an absurd size.
assert.strictEqual(T._normalize('banana'), 100, 'unparseable falls back');
assert.strictEqual(T._normalize('12px'), 100, 'a unit suffix is not a scale');
assert.strictEqual(T._normalize(-400), 100, 'negative clamps to the smallest');
assert.strictEqual(T._normalize(9000), 175, 'huge clamps to the largest');

// Stepping walks one rung at a time and clamps rather than wrapping.
assert.strictEqual(T._step(100, 1), 115);
assert.strictEqual(T._step(115, 1), 130);
assert.strictEqual(T._step(130, -1), 115);
assert.strictEqual(T._step(100, -1), 100, 'the bottom rung does not wrap to the top');
assert.strictEqual(T._step(175, 1), 175, 'the top rung does not wrap to the bottom');
assert.strictEqual(T._step(130, 0), 130, 'no direction means no movement');
assert.strictEqual(T._step('banana', 1), 115, 'a bad current value steps from default');

// Every rung is reachable by pressing the larger button repeatedly.
let pct = T.DEFAULT;
const walked = [pct];
for (let i = 0; i < T.STEPS.length + 2; i++) {
  const next = T._step(pct, 1);
  if (next === pct) break;
  pct = next;
  walked.push(pct);
}
assert.deepStrictEqual(walked, T.STEPS, 'stepping up must reach every step, in order');

// The ladder itself: ascending, no duplicates, and a sane ceiling.
for (let i = 1; i < T.STEPS.length; i++) {
  assert.ok(T.STEPS[i] > T.STEPS[i - 1], 'steps must ascend');
}
assert.ok(T.STEPS[T.STEPS.length - 1] <= 200,
  'a ceiling above 200 percent breaks fixed-px layout');

console.log('test_text_size: ok');
