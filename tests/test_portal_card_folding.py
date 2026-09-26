"""Medium and large cards can be collapsed, and remember it.

Glen, 2026-09-16: "Any medium to large cards need a button to collapse/open. Remember the
last state."

MEASURED, not listed. "Medium to large" is a height, so anything taller than
CARD_FOLD_MIN_PX gets a toggle. A hand-kept list of card ids would drift the first time a
card grew, and this page has 44 cards.

REMEMBERED IN localStorage, and that is the right home rather than a compromise. It is a
per-viewer convenience, it never leaves the browser, and the worst case of losing it is one
click. A server round trip would make a preference into a request.

WIRED DELEGATED AND ONCE. render() replaces app.innerHTML on every background poll, so a
listener bound to a button dies with the button. That is exactly what made the portal
composer's Send button appear dead earlier today, and it is not worth making twice.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "client-portal.html"


def _fn_source(name):
    page = PAGE.read_text()
    at = page.find("\nfunction " + name + "(")
    assert at != -1, f"function {name}() not found"
    end = page.find("\n}", at)
    return page[at:end + 2]


HARNESS = r"""
const assert = require('assert');

const store = {};
let throwOnStorage = false;

function makeEl(tag){
  const el = {
    tagName: tag, id: '', className: '', textContent: '', children: [],
    parentElement: null, offsetHeight: 0, attrs: {}, listeners: {},
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ on ? this._s.add(c) : this._s.delete(c); }
    },
    appendChild(c){ c.parentElement = this; this.children.push(c); return c; },
    setAttribute(k, v){ this.attrs[k] = v; },
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    closest(sel){
      let n = this;
      while (n) {
        if (sel === '.card' && n.className.includes('card') && !n.className.includes('card-fold')) return n;
        if (sel === '.card-fold' && n.className.includes('card-fold')) return n;
        n = n.parentElement;
      }
      return null;
    },
    querySelector(sel){
      if (sel === 'h2,h3') return this.children.find(c => c.tagName === 'H2') || null;
      return null;
    }
  };
  return el;
}

function makeCard(id, headingText, height){
  const card = makeEl('DIV');
  card.className = 'card';
  card.id = id || '';
  card.offsetHeight = height;
  const h = makeEl('H2');
  h.textContent = headingText;
  card.appendChild(h);
  return card;
}

const cards = [];
const document_ = {
  listeners: {},
  querySelectorAll(sel){ return sel === '.card' ? cards : []; },
  createElement(tag){ return makeEl(tag.toUpperCase()); },
  addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
  click(target){ (this.listeners.click || []).forEach(f => f({ target })); }
};
const window_ = {
  localStorage: {
    getItem(k){ if (throwOnStorage) throw new Error('denied'); return k in store ? store[k] : null; },
    setItem(k, v){ if (throwOnStorage) throw new Error('denied'); store[k] = v; },
    removeItem(k){ if (throwOnStorage) throw new Error('denied'); delete store[k]; }
  }
};

// The extracted functions reach for the BARE globals `window` and `document`, as they do
// in a browser. Bind them here or they resolve to node's, which do not exist.
const window = window_;
const document = document_;

__FNS__

// --- a tall card gets a toggle, a small one does not -------------------------
const tall = makeCard('recsCard', 'My Recommendations', 900);
const small = makeCard('', 'A short note', 80);
cards.push(tall, small);
wireCardFolding();

const foldBtn = card => (card.querySelector('h2,h3').children || [])
  .find(c => c.className === 'card-fold');
const btn = foldBtn(tall);
assert.ok(btn, 'a 900px card must get a collapse toggle');
assert.strictEqual(btn.textContent, 'Hide');
assert.ok(!foldBtn(small), 'an 80px card must not get one');

// --- clicking folds it and remembers ----------------------------------------
document_.click(btn);
assert.ok(tall.classList.contains('is-folded'), 'clicking must fold the card');
assert.strictEqual(btn.textContent, 'Show');
assert.strictEqual(store['rm_fold_recsCard'], '1', 'the folded state must be remembered');

// --- clicking again unfolds and forgets -------------------------------------
document_.click(btn);
assert.ok(!tall.classList.contains('is-folded'));
assert.strictEqual(store['rm_fold_recsCard'], undefined,
  'an open card must not leave a stored value behind');

// --- a remembered card comes back folded after a re-render -------------------
store['rm_fold_recsCard'] = '1';
cards.length = 0;
const rebuilt = makeCard('recsCard', 'My Recommendations', 900);
cards.push(rebuilt);
wireCardFolding();
assert.ok(rebuilt.classList.contains('is-folded'),
  'REGRESSION: the remembered state did not survive a render');
assert.strictEqual(foldBtn(rebuilt).textContent, 'Show');

// --- a card with NO id is keyed on its heading -------------------------------
cards.length = 0;
const byHeading = makeCard('', 'Messages & Order Help', 700);
cards.push(byHeading);
wireCardFolding();
document_.click(foldBtn(byHeading));
assert.strictEqual(store['rm_fold_messages-order-help'], '1',
  'a card without an id must be keyed on its heading, not its position');

// --- a card in a hidden door (offsetHeight 0) is not given a toggle ----------
cards.length = 0;
const inHiddenDoor = makeCard('offers-card', 'Offers', 0);
cards.push(inHiddenDoor);
wireCardFolding();
assert.ok(!foldBtn(inHiddenDoor),
  'a card behind a closed door measures 0 and must not be toggled on that basis');

// --- ...unless it is already remembered folded -------------------------------
cards.length = 0;
store['rm_fold_offers-card'] = '1';
const rememberedHidden = makeCard('offers-card', 'Offers', 0);
cards.push(rememberedHidden);
wireCardFolding();
assert.ok(rememberedHidden.classList.contains('is-folded'),
  'a remembered fold must survive even when the card measures 0');

// --- storage that throws must not take the cards down ------------------------
cards.length = 0;
throwOnStorage = true;
const noStorage = makeCard('shop-panel', 'Shop', 800);
cards.push(noStorage);
wireCardFolding();          // must not throw
const b2 = foldBtn(noStorage);
assert.ok(b2, 'the toggle must still appear in a private window');
document_.click(b2);
assert.ok(noStorage.classList.contains('is-folded'),
  'folding must still work when localStorage refuses');

console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_card_folding_behaviour():
    fns = "\n".join(_fn_source(n) for n in
                    ("_cardFoldKey", "_cardFoldRead", "_cardFoldWrite", "_foldWireClickOnce",
                     "wireCardFolding"))
    # CARD_FOLD_MIN_PX is a var, not a function, so it is carried across explicitly.
    # _foldsV2 = false: this is the setting-off (legacy) path the server-backed folds
    # fall back to (tests/test_portal_folds_glue.py covers the other).
    fns = "var CARD_FOLD_MIN_PX = 320;\nvar _foldsV2 = false;\n" + fns
    script = HARNESS.replace("__FNS__", fns)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_the_toggle_is_delegated_and_wired_once():
    """render() rebuilds every card on each poll. A per-button listener dies with it."""
    page = PAGE.read_text()
    legacy = page[page.index("function wireCardFolding()"):]
    legacy = legacy[:legacy.index("\n}") + 2]
    assert "_foldWireClickOnce();" in legacy
    body = page[page.index("function _foldWireClickOnce()"):]
    body = body[:body.index("\n}") + 2]
    assert "__cardFoldWired" in body, "the click handler must be wired once, not per render"
    assert 'document.addEventListener("click"' in body, "it must be delegated on document"


def test_folding_runs_on_every_render():
    page = PAGE.read_text()
    assert "wireEntityRefs(document.getElementById" in page
    assert page.index("wireEntityRefs(document.getElementById") < page.index("  wirePortalFolds();")


def test_a_folded_card_keeps_its_heading_visible():
    """Collapsing must leave something to click, or the card cannot be reopened."""
    page = PAGE.read_text()
    assert ".card.is-folded > *:not(h2):not(h3):not(.card-fold){display:none}" in page
