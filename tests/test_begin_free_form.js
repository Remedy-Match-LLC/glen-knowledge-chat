// tests/test_begin_free_form.js
// Run: node tests/test_begin_free_form.js
//
// The free-membership page (/begin/free). Runs the REAL script from
// static/begin-free.html against a small fake DOM. Both "Become a free member"
// buttons must open the "Send my link" form, and a submit must POST once, to
// /api/healing-oasis/request only, because the page's "How to join" steps describe
// that emailed-link path and no other.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'begin-free.html'), 'utf8');
const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.strictEqual(scripts.length, 1, 'expected one inline script');

function el(id, extra) {
  const e = Object.assign({
    id, hidden: false, disabled: false, readOnly: false, value: '', textContent: '', className: '',
    listeners: {}, valid: true,
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    fire(type, ev) { (this.listeners[type] || []).forEach(fn => fn(Object.assign({
      currentTarget: this, target: this, preventDefault() { this.prevented = true; } }, ev || {}))); },
    focus() { doc.activeElement = this; },
    checkValidity() { return this.valid; },
  }, extra || {});
  return e;
}

let doc, fetches, pending;
function load(enabled) {
  const name = el('oasis-name'), email = el('oasis-email');
  const submit = el('submit'), xBtn = el('x');
  const msg = el('oasis-msg');
  const form = el('oasis-form', {
    querySelector(sel) { assert.strictEqual(sel, '.oasis-submit'); return submit; },
    reset() { name.value = ''; email.value = ''; this.wasReset = true; },
    reportValidity() { this.reported = true; },
  });
  const modal = el('oasis-modal', { hidden: true,
    querySelector(sel) { assert.strictEqual(sel, '.oasis-x'); return xBtn; } });
  const join = [el('join1', { parentNode: { hidden: false } }), el('join2', { parentNode: { hidden: false } })];
  const byId = { 'oasis-modal': modal, 'oasis-form': form, 'oasis-msg': msg,
                 'oasis-name': name, 'oasis-email': email };
  doc = el('document', {
    activeElement: null,
    getElementById(id) { return byId[id] || null; },
    querySelectorAll(sel) { assert.strictEqual(sel, '.join-btn'); return join; },
  });
  fetches = []; pending = [];
  const ctx = {
    document: doc, JSON, setTimeout: fn => fn(),
    fetch(url, opts) {
      if (url === '/api/healing-oasis/status' && !opts) {
        return Promise.resolve({ json: () => Promise.resolve({ enabled: enabled !== false }) });
      }
      fetches.push({ url, opts });
      return new Promise(resolve => pending.push(resolve));
    },
  };
  vm.createContext(ctx);
  vm.runInContext(scripts[0], ctx);
  return { name, email, submit, xBtn, msg, form, modal, join };
}
function reply(body, ok) {
  const r = pending.shift();
  const status = typeof ok === 'number' ? ok : (ok === false ? 400 : 200);
  r({ ok: status < 300, status, json: () => Promise.resolve(body) });
  return new Promise(res => setImmediate(res));
}

(async () => {
  // 1. Each join button opens the form.
  for (const i of [0, 1]) {
    const p = load();
    assert.strictEqual(p.modal.hidden, true);
    p.join[i].fire('click');
    assert.strictEqual(p.modal.hidden, false, `button ${i + 1} did not open the form`);
    assert.strictEqual(doc.activeElement, p.name, 'focus goes to the name field');
  }

  // 2. A valid submit POSTs once, to the Send my link endpoint, with name and email.
  let p = load();
  p.join[0].fire('click');
  p.name.value = ' Test Person '; p.email.value = 'person@example.test';
  p.form.fire('submit');
  p.form.fire('submit');                    // a second press while sending is ignored
  assert.strictEqual(fetches.length, 1);
  assert.strictEqual(fetches[0].url, '/api/healing-oasis/request');
  assert.strictEqual(fetches[0].opts.method, 'POST');
  assert.deepStrictEqual(JSON.parse(fetches[0].opts.body), { name: 'Test Person', email: 'person@example.test' });
  assert.strictEqual(p.name.readOnly, true, 'fields are locked while sending');
  await reply({ ok: true, message: 'Check your email for your link.' });
  assert.strictEqual(p.msg.textContent, 'Check your email for your link.');
  assert.strictEqual(p.form.wasReset, true);
  assert.strictEqual(p.name.readOnly, false);
  assert.strictEqual(p.submit.disabled, false);

  // 3. A blank name or a bad email never posts.
  p = load();
  p.join[1].fire('click');
  p.name.valid = false; p.email.value = 'person@example.test';
  p.form.fire('submit');
  assert.strictEqual(fetches.length, 0);
  assert.strictEqual(p.form.reported, true);
  p.name.valid = true; p.email.value = 'a@'; p.email.valid = false;
  p.form.fire('submit');
  assert.strictEqual(fetches.length, 0);
  assert.strictEqual(p.msg.textContent, 'Please enter a valid email address.');

  // 4. Escape closes, and Tab stays inside the open dialog.
  p = load();
  p.join[0].fire('click');
  p.submit.focus();
  let ev = { key: 'Tab', shiftKey: false, prevented: false, preventDefault() { this.prevented = true; } };
  (doc.listeners.keydown || []).forEach(fn => fn(ev));
  assert.strictEqual(ev.prevented, true);
  assert.strictEqual(doc.activeElement, p.xBtn, 'Tab from the last control wraps to the first');
  doc.activeElement = p.join[1];            // focus somehow behind the overlay
  ev = { key: 'Tab', shiftKey: true, prevented: false, preventDefault() { this.prevented = true; } };
  (doc.listeners.keydown || []).forEach(fn => fn(ev));
  assert.strictEqual(doc.activeElement, p.submit);
  (doc.listeners.keydown || []).forEach(fn => fn({ key: 'Escape' }));
  assert.strictEqual(p.modal.hidden, true);
  assert.strictEqual(doc.activeElement, p.join[0], 'focus returns to the button that opened it');

  // 5. A server error is shown and the form unlocks.
  p = load();
  p.join[0].fire('click');
  p.name.value = 'A'; p.email.value = 'person@example.test';
  p.form.fire('submit');
  await reply({ ok: false, error: 'Please enter a valid email address.' }, false);
  assert.strictEqual(p.msg.textContent, 'Please enter a valid email address.');
  assert.strictEqual(p.submit.disabled, false);

  // 6. With sign-up switched off the endpoint says "not found". Show the plain line.
  p = load();
  p.join[0].fire('click');
  p.name.value = 'A'; p.email.value = 'person@example.test';
  p.form.fire('submit');
  await reply({ error: 'not found' }, 404);
  assert.strictEqual(p.msg.textContent, 'Something went wrong. Please try again.');

  // 7. When sign-up is switched off, both join buttons are hidden.
  p = load(false);
  await new Promise(res => setImmediate(res));
  assert.deepStrictEqual(p.join.map(b => b.parentNode.hidden), [true, true]);
  p = load(true);
  await new Promise(res => setImmediate(res));
  assert.deepStrictEqual(p.join.map(b => b.parentNode.hidden), [false, false]);

  console.log('ok - begin-free form: 7 checks');
})().catch(e => { console.error(e); process.exit(1); });
