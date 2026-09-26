"""The portal's server-backed card folding, run in node against a small fake DOM.

Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md. The 2026-09-16 version
showed no toggles live because it measured cards inside hidden doors, and keyed folds on
heading text that included the button's own label.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "client-portal.html"

GLUE = ("foldSlug", "_foldWireClickOnce", "_foldWireLifecycleOnce", "_foldIdOf",
        "_foldCardsByDoor", "_foldDoorVisible", "_foldSetCard", "_foldApplyAll", "_foldToggle",
        "_foldClearLegacy", "_foldLoad", "_foldRefresh", "_foldSave", "_foldPut", "_foldFlush",
        "wirePortalFolds", "_foldBars", "_foldBarClick", "_foldAllOrRestore", "_foldMatchClient")


def _fn_source(name):
    page = PAGE.read_text()
    at = page.find("\nfunction " + name + "(")
    assert at != -1, f"function {name}() not found"
    end = page.find("\n}", at)
    return page[at:end + 2]


def _vars():
    page = PAGE.read_text()
    return "\n".join(re.findall(r"^var _(?:fold|FOLD)\w*[^\n]*;", page, re.M))


HARNESS = r"""
const assert = require('assert');

// ── a small fake DOM ────────────────────────────────────────────────────────
let all = [];
function el(tag, props){
  const e = {tagName: tag, children: [], parentElement: null, hidden: false, dataset: {},
    attrs: {}, textContent: '', className: '', listeners: {},
    classList: {
      _s: new Set(), add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ (on === undefined ? !this._s.has(c) : on) ? this._s.add(c) : this._s.delete(c); }
    },
    appendChild(c){ c.parentElement = this; this.children.push(c); all.push(c); return c; },
    insertBefore(c, ref){ c.parentElement = this; const i = this.children.indexOf(ref);
      i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); all.push(c); return c; },
    get firstChild(){ return this.children[0] || null; },
    setAttribute(k, v){ this.attrs[k] = String(v); if (k.startsWith('data-'))
      this.dataset[k.slice(5).replace(/-(\w)/g, (_, x) => x.toUpperCase())] = String(v); },
    getAttribute(k){ return this.attrs[k]; },
    matches(sel){ return matches(this, sel); },
    closest(sel){ let n = this; while (n) { if (matches(n, sel)) return n; n = n.parentElement; } return null; },
    querySelector(sel){ return desc(this).find(n => matches(n, sel)) || null; },
    querySelectorAll(sel){ return desc(this).filter(n => matches(n, sel)); },
  };
  Object.assign(e, props || {});
  if (props && props.cls) props.cls.split(' ').forEach(c => e.classList.add(c));
  return e;
}
function desc(n){ const out = []; (function walk(x){ x.children.forEach(c => { out.push(c); walk(c); }); })(n); return out; }
function matches(n, sel){
  return sel.split(',').some(s => {
    s = s.trim();
    if (s === 'h2' || s === 'h3') return n.tagName === s.toUpperCase();
    if (s === '[data-door]') return n.dataset.door !== undefined;
    let m = s.match(/^section\[data-door="(.*)"\]$/);
    if (m) return n.tagName === 'SECTION' && n.dataset.door === m[1];
    if (s.startsWith('.')) return n.classList.contains(s.slice(1));
    return false;
  });
}
const body = el('BODY');
const listeners = {click: [], visibilitychange: []};
const document = {
  visibilityState: 'visible',
  querySelectorAll(sel){ return body.querySelectorAll(sel); },
  querySelector(sel){ return body.querySelector(sel); },
  createElement(tag){ return el(tag.toUpperCase()); },
  addEventListener(t, f){ (listeners[t] = listeners[t] || []).push(f); },
};
const winListeners = {};
const store = {rm_fold_x: '1', other: 'keep'};
const window = {
  PortalFolds: require(__ROOT__ + '/static/js/portal-folds.js'), addEventListener(t, f){ (winListeners[t] = winListeners[t] || []).push(f); },
  localStorage: {
    get length(){ return Object.keys(store).length; },
    key(i){ return Object.keys(store)[i]; },
    removeItem(k){ delete store[k]; }, getItem(k){ return store[k] ?? null; }, setItem(k, v){ store[k] = v; },
  },
};
function click(target){ listeners.click.forEach(f => f({target})); }

// ── fetch and timers ────────────────────────────────────────────────────────
const calls = [];
let reply = {status: 200, body: {ok: true, viewer: 'client', state: {cards: {}, seen: [], before_fold_all: {}}}};
let replyFor = null;          // optional (url, opts) -> reply
function fetch(url, opts){
  calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body, keepalive: !!(opts && opts.keepalive)});
  const r = replyFor ? (replyFor(url, opts) || reply) : reply;
  return Promise.resolve({status: r.status, ok: r.status < 300, json: () => Promise.resolve(r.body)});
}
let timers = [];
function setTimeout(f){ timers.push(f); return timers.length; }
function clearTimeout(id){ if (id) timers[id - 1] = null; }
function runTimers(){ const t = timers; timers = []; t.forEach(f => f && f()); }
const settle = () => new Promise(r => global.setTimeout(r, 5));

let legacyCalls = 0;
function wireCardFolding(){ legacyCalls++; }
const token = 'TOKEN';

// ── a page: one door section with three cards, plus odd cases ───────────────
function card(id, heading, extra){
  const c = el('DIV', {cls: 'card'});
  if (id) c.setAttribute('data-fold-id', id);
  if (heading) c.appendChild(el('H2', {textContent: heading}));
  c.appendChild(el('P', {textContent: 'body'}));
  Object.assign(c.dataset, extra || {});
  return c;
}
function page(){
  all = []; body.children = [];
  const sec = body.appendChild(el('SECTION')); sec.setAttribute('data-door', 'scans');
  const cards = {a: card('a', 'A'), b: card('b', 'B'), c: card('c', 'C'),
                 skip: card('cal', 'Live', {foldSkip: '1'}), bare: card('bare', null)};
  Object.values(cards).forEach(x => sec.appendChild(x));
  return cards;
}
const toggles = c => c.children.filter(x => x.classList.contains('card-fold'));
const putCalls = () => calls.filter(c => c.method === 'PUT');

__VARS__
__FNS__

(async () => {
  // 1. first render loads the record once
  let p = page();
  wirePortalFolds();
  assert.strictEqual(calls.filter(c => c.method === 'GET').length, 1);
  assert.strictEqual(calls[0].url, '/api/portal/TOKEN/folds');
  await settle(); await settle();

  // 2. every titled card has exactly one toggle; first open, rest folded; seen + save
  for (const k of ['a', 'b', 'c']) assert.strictEqual(toggles(p[k]).length, 1, k);
  assert.ok(!p.a.classList.contains('is-folded'));
  assert.ok(p.b.classList.contains('is-folded') && p.c.classList.contains('is-folded'));
  assert.strictEqual(toggles(p.b)[0].textContent, 'Show');
  assert.deepStrictEqual(_foldsV2.state.seen, ['scans']);
  runTimers();
  assert.strictEqual(putCalls().length, 1);

  // 3. a card measuring 0px (hidden door) still gets its toggle: the 09-16 defect
  assert.strictEqual(toggles(p.c).length, 1);

  // 4. skipped and headless cards get none
  assert.strictEqual(toggles(p.skip).length, 0);
  assert.strictEqual(toggles(p.bare).length, 0);

  // 5. clicking b opens it and saves after the debounce
  calls.length = 0;
  click(toggles(p.b)[0]);
  assert.ok(!p.b.classList.contains('is-folded'));
  assert.strictEqual(putCalls().length, 0, 'saved before the debounce');
  runTimers();
  assert.strictEqual(putCalls().length, 1);
  assert.strictEqual(JSON.parse(putCalls()[0].body).state.cards.b, false);

  // 6. three quick clicks send one PUT
  calls.length = 0;
  click(toggles(p.a)[0]); click(toggles(p.a)[0]); click(toggles(p.a)[0]);
  runTimers();
  assert.strictEqual(putCalls().length, 1);

  // 7. a re-render re-applies the same state with no new GET
  calls.length = 0;
  const state = JSON.stringify(_foldsV2.state);
  p = page();
  wirePortalFolds();
  assert.strictEqual(calls.filter(c => c.method === 'GET').length, 0);
  assert.strictEqual(JSON.stringify(_foldsV2.state), state);
  assert.ok(p.a.classList.contains('is-folded') && !p.b.classList.contains('is-folded'));

  // 10. flush on pagehide sends the pending save at once, with keepalive
  calls.length = 0;
  click(toggles(p.c)[0]);
  winListeners.pagehide.forEach(f => f());
  assert.strictEqual(putCalls().length, 1);
  assert.ok(putCalls()[0].keepalive);
  runTimers();
  assert.strictEqual(putCalls().length, 1, 'the flushed save was sent twice');

  // 11. legacy browser folds were cleared, other keys kept
  assert.ok(!('rm_fold_x' in store) && store.other === 'keep');

  // 12. coming back to the tab re-reads the record (another device may have changed it)
  calls.length = 0;
  reply = {status: 200, body: {ok: true, viewer: 'client', state: {cards: {a: false, b: true, c: true}, seen: ['scans'], before_fold_all: {}}}};
  listeners.visibilitychange.forEach(f => f());
  await settle(); await settle();
  assert.strictEqual(calls.filter(c => c.method === 'GET').length, 1);
  assert.ok(!p.a.classList.contains('is-folded') && p.b.classList.contains('is-folded'));

  // 8. setting off: a 404 runs the legacy path
  _foldsV2 = undefined; _foldLoading = false; calls.length = 0;
  reply = {status: 404, body: {error: 'not found'}};
  page(); wirePortalFolds(); await settle(); await settle();
  assert.strictEqual(_foldsV2, false);
  assert.strictEqual(legacyCalls, 1);
  page(); wirePortalFolds();
  assert.strictEqual(legacyCalls, 2, 'legacy must run on every render once chosen');

  // 9. a server error leaves it unknown, and the next render tries again
  _foldsV2 = undefined; _foldLoading = false; calls.length = 0;
  reply = {status: 500, body: {}};
  page(); wirePortalFolds(); await settle(); await settle();
  assert.strictEqual(_foldsV2, undefined);
  page(); wirePortalFolds();
  assert.strictEqual(calls.filter(c => c.method === 'GET').length, 2);

  __TASK6__
  console.log('OK');
})().catch(e => { console.error(e); process.exit(1); });
"""

TASK6 = r"""
  // ── Task 6: the page bar ─────────────────────────────────────────────────
  _foldsV2 = undefined; _foldLoading = false; calls.length = 0; replyFor = null;
  reply = {status: 200, body: {ok: true, viewer: 'client', state: {cards: {}, seen: [], before_fold_all: {}}}};
  p = page(); wirePortalFolds(); await settle(); await settle();
  const bars = () => body.children[0].children.filter(x => x.classList.contains('fold-bar'));
  const btn = (cls) => bars()[0].children.find(x => x.classList.contains(cls));

  // 1. a client sees one bar with Fold all and no match button
  assert.strictEqual(bars().length, 1);
  assert.strictEqual(btn('fold-bar-all').textContent, 'Fold all');
  assert.ok(!btn('fold-bar-match'));

  // 2. Fold all, then Restore returns the exact earlier states
  const before = ['a', 'b', 'c'].map(k => p[k].classList.contains('is-folded'));
  click(btn('fold-bar-all'));
  assert.ok(['a', 'b', 'c'].every(k => p[k].classList.contains('is-folded')));
  assert.strictEqual(btn('fold-bar-all').textContent, 'Restore');
  click(btn('fold-bar-all'));
  assert.deepStrictEqual(['a', 'b', 'c'].map(k => p[k].classList.contains('is-folded')), before);
  assert.strictEqual(btn('fold-bar-all').textContent, 'Fold all');

  // 3. a re-render does not duplicate the bar
  wirePortalFolds(); wirePortalFolds();
  assert.strictEqual(bars().length, 1);

  // 4. staff see Match client's view; it reads ?of=client and saves as the staff record
  _foldsV2 = undefined; _foldLoading = false; calls.length = 0; runTimers(); calls.length = 0;
  const clientState = {cards: {a: true, b: false, c: false}, seen: ['scans'], before_fold_all: {}};
  replyFor = (url) => url.endsWith('?of=client')
    ? {status: 200, body: {ok: true, viewer: 'staff', state: clientState}}
    : {status: 200, body: {ok: true, viewer: 'staff', state: {cards: {}, seen: [], before_fold_all: {}}}};
  p = page(); wirePortalFolds(); await settle(); await settle();
  assert.ok(btn('fold-bar-match'));
  assert.strictEqual(btn('fold-bar-match').textContent, "Match client's view");
  runTimers(); calls.length = 0;
  click(btn('fold-bar-match')); await settle(); await settle();
  assert.ok(calls.some(c => c.method === 'GET' && c.url.endsWith('?of=client')));
  assert.deepStrictEqual(['a', 'b', 'c'].map(k => p[k].classList.contains('is-folded')), [true, false, false]);
  runTimers();
  const put = putCalls().pop();
  assert.ok(put && !put.url.includes('of='));
  assert.deepStrictEqual(JSON.parse(put.body).state.cards, clientState.cards);

  // 5. a failed match leaves the staff view alone
  const kept = JSON.stringify(_foldsV2.state);
  replyFor = (url) => url.endsWith('?of=client') ? {status: 500, body: {}} : null;
  click(btn('fold-bar-match')); await settle(); await settle();
  assert.strictEqual(JSON.stringify(_foldsV2.state), kept);
  assert.ok(btn('fold-bar-match').attrs.title, 'the failure must be visible on the button');
"""


def _script(task6=""):
    fns = "\n".join(_fn_source(n) for n in GLUE)
    return (HARNESS.replace("__ROOT__", repr(str(ROOT))).replace("__VARS__", _vars())
            .replace("__FNS__", fns).replace("__TASK6__", task6))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_server_backed_folding_behaviour(tmp_path):
    # A file, not `node -e`: under -e the harness's `const window` is a global binding the
    # required module sees before it is initialised.
    js = tmp_path / "glue.js"
    js.write_text(_script(TASK6))
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_a_folded_card_keeps_its_toggle_visible():
    assert ".card.is-folded > *:not(h2):not(h3):not(.card-fold){display:none}" in PAGE.read_text()


def test_the_page_loads_the_rules_module():
    assert '<script src="/static/js/portal-folds.js"></script>' in PAGE.read_text()


def test_every_render_runs_the_new_wiring():
    page = PAGE.read_text()
    assert page.index("wireEntityRefs(document.getElementById") < page.index("  wirePortalFolds();")
