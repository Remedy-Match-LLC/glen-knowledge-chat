// tests/test_five_voice_recorder_timing.js
// Run: node tests/test_five_voice_recorder_timing.js
//
// Glen, 2026-09-18, for the Five Element Voice Analysis (then "Voice Scan") in the client portal:
//   1. Stop is unavailable for the first 20 seconds.
//   2. The timer reads "Ns, about 60s".
//   3. The recording still stops itself at 90 seconds.
// The real initFiveElementVoice is lifted out of client-portal.html and run against a
// fake DOM, microphone, recorder and clock, so these assert behaviour, not source text.
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
function lift(re, what) {
  const m = html.match(re);
  assert.ok(m, `could not find ${what} in client-portal.html`);
  return m[0];
}
const consts = lift(/const FIVE_VOICE_MIN_S[^\n]*\n/, 'the timing constants');
const start = html.indexOf('function initFiveElementVoice(){');
assert.ok(start >= 0, 'initFiveElementVoice missing');
const end = html.indexOf('\nfunction render(', start);
const initSrc = html.slice(start, end);

async function scenario() {
  let now = 1_000_000;
  const intervals = [];
  const els = {};
  const button = id => (els[id] = { id, hidden: false, disabled: false, textContent: '', dataset: {},
    handlers: {}, addEventListener(ev, fn) { this.handlers[ev] = fn; },
    click() { return this.handlers.click && this.handlers.click(); } });
  button('fiveVoiceRecord'); button('fiveVoiceStop'); button('fiveVoiceTimer');
  const rec = { state: 'inactive', mimeType: 'audio/webm', stops: 0, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    start() { this.state = 'recording'; },
    stop() { this.stops++; this.state = 'inactive'; this.listeners.stop && this.listeners.stop(); } };
  const submitted = [];
  const sandbox = {
    document: { getElementById: id => els[id] },
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
    window: { MediaRecorder: true },
    MediaRecorder: function () { return rec; },
    Blob: function () {},
    Date: { now: () => now },
    setInterval: fn => { intervals.push(fn); return intervals.length; },
    clearInterval: () => {},
    fiveVoiceSetStatus: () => {},
    fiveVoiceResetControls: () => {},
    fiveVoiceSubmit: (b, d) => submitted.push(d),
  };
  const fn = new Function(...Object.keys(sandbox),
    'let _fiveVoiceStream=null,_fiveVoiceChunks=[],_fiveVoiceRecorder=null,' +
    '_fiveVoiceStartedAt=0,_fiveVoiceTimerId=null;\n' + consts + initSrc +
    '\nreturn initFiveElementVoice;');
  fn(...Object.values(sandbox))();
  await els.fiveVoiceRecord.click();
  const tick = s => { now = 1_000_000 + s * 1000; intervals.forEach(f => f()); };
  return { els, rec, submitted, tick };
}

(async () => {
  const t = await scenario();
  const stop = t.els.fiveVoiceStop;

  t.tick(0);
  assert.strictEqual(t.els.fiveVoiceTimer.textContent, '0s, about 60s');
  assert.strictEqual(stop.disabled, true, 'Stop must be unavailable at the start');

  t.tick(19);
  stop.click();
  assert.strictEqual(t.rec.stops, 0, 'a click before 20s must not stop the recording');
  assert.strictEqual(stop.disabled, true);

  t.tick(20);
  assert.strictEqual(stop.disabled, false, 'Stop becomes available at 20s');
  assert.strictEqual(t.els.fiveVoiceTimer.textContent, '20s, about 60s');
  stop.click();
  assert.strictEqual(t.rec.stops, 1, 'a click at 20s stops it');
  assert.strictEqual(t.submitted.length, 1);

  const t2 = await scenario();
  t2.tick(89);
  assert.strictEqual(t2.rec.stops, 0);
  t2.tick(90);
  assert.strictEqual(t2.rec.stops, 1, 'the recording stops itself at 90s');

  console.log('test_five_voice_recorder_timing: ok');
})().catch(e => { console.error(e); process.exit(1); });
