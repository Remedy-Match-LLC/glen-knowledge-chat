"""The "review your order" path lands on the Order your remedies card open.

openPortalOrderBasket() scrolled to #portal-order-basket but left it folded, so a client
who had folded it saw a heading and no pay step (Donna, 2026-11-03). These run the real
function in node with the page's real fold helpers and a stub DOM, for both fold modes:
the shared fold state (PORTAL_FOLDS_V2) and the older per-browser folding.
"""
import json
import re
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "client-portal.html").read_text()
FOLDS = (ROOT / "static" / "js" / "portal-folds.js").read_text()


def _fn(name):
    at = PAGE.index("\nfunction " + name + "(")
    return PAGE[at:PAGE.index("\n}", at) + 2]


STUBS = r"""
var store = {}, calls = [], scrolled = 0;
var window = {setTimeout: function(f){ f(); }, scrollY: 0,
              localStorage: {getItem: function(k){ return store[k] || null; },
                             setItem: function(k, v){ store[k] = v; },
                             removeItem: function(k){ delete store[k]; }}};
var sessionStorage = {setItem: function(){}};
var puts = [];
function setTimeout(f){ calls.push("timer"); f(); return 1; }
function clearTimeout(){}
function mkClass(o){ return {contains: function(c){ return !!o.cls[c]; },
                             remove: function(c){ delete o.cls[c]; },
                             add: function(c){ o.cls[c] = true; },
                             toggle: function(c, on){ if(on) o.cls[c] = true; else delete o.cls[c]; }}; }
var btn = {cls: {"card-fold": true}, textContent: "Show", attrs: {"aria-expanded": "false"},
           setAttribute: function(k, v){ this.attrs[k] = v; }};
btn.classList = mkClass(btn);
var card = {id: "portal-order-basket", dataset: {}, cls: {"card": true, "is-folded": true},
            children: [btn],
            querySelector: function(sel){ return sel === ".card-fold" ? btn : null; },
            scrollIntoView: function(){ scrolled++; }};
card.classList = mkClass(card);
var document = {getElementById: function(id){ return id === "portal-order-basket" ? card : null; },
                querySelectorAll: function(){ return []; }, querySelector: function(){ return null; }};
function showTab(p){ calls.push("showTab:" + p); }
function portalPanelFor(r){ return r.shellPanel; }
function _foldCardsByDoor(){ return {remedies: [card]}; }
function _foldDoorVisible(){ return true; }
function fetch(url, opts){ puts.push(JSON.parse(opts.body).state.cards["portal-order-basket"]);
                       return {catch: function(){}}; }
var token = "t";
"""


FOLDED = ("window.PortalFolds.setCard(window.PortalFolds.markSeen(window.PortalFolds.emptyState(),"
          " 'remedies', ['portal-order-basket'], []), 'portal-order-basket', %s)")
REPORT = ("process.stdout.write(JSON.stringify({folded: card.classList.contains('is-folded'),"
          " pending: window._foldDeepLinkOpen || null, stored: store['rm_fold_portal-order-basket'] || null,"
          " btn: btn.textContent, expanded: btn.attrs['aria-expanded'], scrolled: scrolled,"
          " saved: puts.length ? puts[puts.length - 1] === false : false, puts: puts,"
          " state: _foldsV2 && _foldsV2.state ? _foldsV2.state.cards['portal-order-basket'] : null}));")


def _run(setup, after=""):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    decl = re.search(r"^var _foldRev = .*$", PAGE, re.M).group(0)
    js = "\n".join([
        STUBS,
        "(function(){ var module; " + FOLDS + " })();",
        decl, "var _foldSavePending = false, _foldSaveTimer = null, _FOLD_SAVE_MS = 1;",
        setup,
        *[_fn(n) for n in ("_foldIdOf", "_foldSetCard", "_foldApplyAll", "_foldOpenCard",
                           "_foldToggle", "_foldSave", "_foldPut", "_cardFoldKey",
                           "_cardFoldWrite", "rememberPortalShoppingContext",
                           "openPortalOrderBasket")],
        "openPortalOrderBasket(false);",
        after,
        REPORT,
    ])
    js = js.replace("var _FOLD_HEADS", "var _FOLD_HEADS_UNUSED")
    js = "var _FOLD_HEADS = 'h2,h3,.glabel';\n" + js
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_folded_basket_opens_with_shared_fold_state():
    r = _run("var _foldsV2 = {state: " + FOLDED % "true" + ", staff: false};")
    assert r["folded"] is False
    assert r["state"] is False      # saved open, so a reload keeps it open
    assert r["saved"] is True
    assert r["pending"] is None
    assert r["scrolled"] == 1


def test_an_open_basket_leaves_no_request_to_undo_a_later_hide():
    # Already open on the page, as the saved state says.
    r = _run("var _foldsV2 = {state: " + FOLDED % "false" + ", staff: false};"
             " delete card.cls['is-folded'];",
             after="_foldToggle(card);")
    assert r["pending"] is None
    assert r["folded"] is True      # the client's Hide holds
    assert r["state"] is True
    assert r["puts"][-1] is True    # and is what gets saved


def test_fold_state_that_arrives_later_opens_the_card_and_saves_it():
    r = _run("var _foldsV2 = null;",
             after="_foldsV2 = {state: " + FOLDED % "true" + ", staff: false}; _foldApplyAll();")
    assert r["folded"] is False
    assert r["state"] is False
    assert r["saved"] is True
    assert r["pending"] is None


def test_a_folded_basket_opens_with_per_browser_folding():
    r = _run("var _foldsV2 = false; store['rm_fold_portal-order-basket'] = '1';")
    assert r["folded"] is False
    assert r["stored"] is None      # the remembered fold is cleared
    assert r["btn"] == "Hide" and r["expanded"] == "true"
    assert r["pending"] is None
    assert r["scrolled"] == 1


def test_a_remembered_fold_is_cleared_before_folds_load():
    # The load may answer 404 and hand over to the per-browser folding.
    r = _run("var _foldsV2; store['rm_fold_portal-order-basket'] = '1';")
    assert r["stored"] is None
    assert r["folded"] is False     # open on the page even if the load stalls
    assert r["pending"] == "portal-order-basket"
