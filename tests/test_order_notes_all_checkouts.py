"""The Microbiome order note on the four remaining checkouts (Glen, 2026-10-13).

#1995 put the note on the store cart, the portal basket and its reorder card. Glen then
said yes to the rest: the single-product buy page, the practitioner checkouts (wholesale,
personal and drop-ship), the client checkout and the invoice pay page. Each Zelle or Wise
reply carries order_notes, and each page shows it in its own card after the Zelle box.
A practitioner card payment returns to the practitioner page, so the note is kept in
sessionStorage across the Stripe round trip and shown on return."""
import json
import os
import re
import shutil
import subprocess

import pytest

import app as appmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S1 = "It is beneficial to open the capsule and take a small amount of the powder straight into your mouth."
S2 = "Then close the capsule and swallow it, so the rest of the powder reaches your intestines."
CART = [{"slug": "microbiome", "qty": 1}]


def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def _assert_microbiome(notes):
    assert len(notes) == 1 and notes[0]["paras"] == [S1, S2]
    assert "microbiome" in notes[0]["name"].lower()


# ── Practitioner checkouts ────────────────────────────────────────────────────
@pytest.fixture
def prac(monkeypatch):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "p1")
    monkeypatch.setattr(appmod._pp, "portal_data", lambda pid: {
        "cart": list(CART), "modules_completed": 3, "email": "doc@x.com", "name": "Doc",
        "wholesale_unlocked": True, "resale_license_number": None, "wallet_balance_cents": 0})
    order = {"ok": True, "invoice_id": "INV1", "doc_number": "1001", "total": 60.0,
             "get_cents": 0, "credit_redeemed_cents": 0}
    monkeypatch.setattr(appmod._wc, "build_order", lambda *a, **k: dict(order))
    monkeypatch.setattr(appmod._dropship, "build_dropship_order", lambda *a, **k: dict(
        order, customer_id="C1", source="dropship", ship_to=k.get("patient_ship"),
        method="zelle", shipping_cents=k.get("shipping_cents")))
    monkeypatch.setattr(appmod, "_price_cart", lambda *a, **k: {"shipping_cents": 1300})
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(appmod._wallet, "earn_personal", lambda *a, **k: 0)
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: None)
    monkeypatch.setattr(appmod._pp, "record_order", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_stripe_checkout_url_for_order", lambda *a, **k: "https://stripe.test/x")
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", True)
    return appmod.app.test_client()


ADDR = {"street": "1 Main St", "city": "Hilo", "state": "HI", "zip": "96720", "country": "US"}


@pytest.mark.parametrize("path", ["/api/practitioner/checkout", "/api/practitioner/personal/checkout"])
@pytest.mark.parametrize("method", ["zelle", "card"])
def test_a_practitioner_checkout_carries_the_note(prac, path, method):
    r = prac.post(path, json={"method": method, "address": ADDR})
    assert r.status_code == 200, r.get_json()
    _assert_microbiome(r.get_json()["order_notes"])


def test_the_dropship_checkout_carries_the_note(prac):
    r = prac.post("/api/practitioner/dropship/checkout", json={
        "method": "zelle", "recipient_name_confirmed": True,
        "patient_address": dict(ADDR, name="Pat")})
    assert r.status_code == 200, r.get_json()
    _assert_microbiome(r.get_json()["order_notes"])


def test_a_cart_without_microbiome_carries_no_note(prac, monkeypatch):
    monkeypatch.setattr(appmod._pp, "portal_data", lambda pid: {
        "cart": [{"slug": "brain-boost", "qty": 2}], "modules_completed": 3,
        "email": "doc@x.com", "name": "Doc", "wholesale_unlocked": True})
    r = prac.post("/api/practitioner/checkout", json={"method": "zelle", "address": ADDR})
    assert r.get_json()["order_notes"] == []


# ── Invoice pay page ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("items,expect", [(CART, True), ([{"slug": "microbiome", "qty": 0}], False)])
def test_the_invoice_zelle_reply_carries_the_note(monkeypatch, items, expect):
    monkeypatch.setattr(appmod, "_invoice_order_for_token", lambda t: {
        "id": 7, "status": "proposed", "pay_status": "unpaid", "total_cents": 6000,
        "items": items})
    monkeypatch.setattr(appmod, "INVOICE_PAYLINK_ENABLED", True)
    r = appmod.app.test_client().post("/api/invoice/tok/pay", json={"method": "zelle"})
    assert r.status_code == 200
    notes = r.get_json()["order_notes"]
    if expect:
        _assert_microbiome(notes)
    else:
        assert notes == []


# ── Buy page and client checkout: the reply sets the note in its Zelle branch ──
def _route_body(name):
    src = _read("app.py")
    i = src.index(f"def {name}(")
    j = src.index("\n@app.route(", i)
    return src[i:j]


def test_the_buy_page_zelle_branch_sets_the_note():
    src = _read("app.py")
    k = src.index('"note": _BUY_PAGE_ALT_PAY_NOTE.get(method, "")}')
    assert 'out["order_notes"] = _order_notes_for(pc["items_rec"])' in src[k:k + 200]


def test_the_client_checkout_zelle_branch_sets_the_note():
    body = _route_body("api_client_checkout")
    branch = body[body.index('if method in ("zelle", "wise"):'):]
    branch = branch[:branch.index("elif method")]
    assert 'out["order_notes"] = _order_notes_for(items)' in branch


# ── The pages ─────────────────────────────────────────────────────────────────
PAGES = ["static/begin-buy.html", "static/invoice.html", "static/practitioner-client.html",
         "static/practitioner-portal.html", "static/practitioner-dropship.html"]


@pytest.mark.parametrize("page", PAGES)
def test_each_page_loads_the_helper_in_its_head_and_renders_the_note(page):
    src = _read(page)
    head = src[:src.index("</head>")]
    assert '<script src="/static/js/order-notes.js"></script>' in head
    assert re.search(r"window\.renderOrderNotes\) renderOrderNotes\(", src)
    assert 'id="order-notes"' in src


@pytest.mark.parametrize("page", ["static/practitioner-portal.html", "static/practitioner-dropship.html"])
def test_a_practitioner_card_payment_keeps_the_note_for_its_own_return(page):
    src = _read(page)
    branch = src[src.index("if(d.stripe_url){\n"):]
    branch = branch[:branch.index("return;")]
    assert "keepOrderNotes(d.order_notes, d.invoice_id)" in branch
    # Without a redirect the note comes straight from the reply, never via storage
    # (review round 2: blocked storage hid it).
    assert "renderOrderNotes($('order-notes'), d.order_notes)" in src
    ret = src.index("get('paid')")
    assert re.search(r"takeOrderNotes\((ref|new URLSearchParams\(location\.search\)\.get\('order'\))\)",
                     src[ret:ret + 900])


JS = r"""
const assert = require('assert');
function el(tag){ return {tag, children: [], hidden: false, textContent: '', ownerDocument: doc,
  classList: {_s: new Set(), toggle(c, on){ on ? this._s.add(c) : this._s.delete(c); }},
  appendChild(c){ this.children.push(c); return c; },
  removeChild(c){ this.children.splice(this.children.indexOf(c), 1); },
  get firstChild(){ return this.children[0]; } }; }
const doc = { createElement: el };
const store = {};
globalThis.sessionStorage = { getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
SRC
const host = el('div');
const NOTES = NOTES_JSON;
assert.strictEqual(renderOrderNotes(host, NOTES), 1);
assert.strictEqual(host.hidden, false);
assert.strictEqual(host.children[0].children[0].tag, 'strong');
assert.strictEqual(host.children[0].children[0].textContent, NOTES[0].name);
assert.deepStrictEqual(host.children.slice(1).map(p => p.textContent), NOTES[0].paras);
// A name is text, never markup.
renderOrderNotes(host, [{name: '<img src=x onerror=alert(1)>', paras: ['a']}]);
assert.strictEqual(host.children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
// Nothing to show hides the card and clears the old one.
assert.strictEqual(renderOrderNotes(host, []), 0);
assert.strictEqual(host.hidden, true);
assert.strictEqual(host.children.length, 0);
assert.strictEqual(renderOrderNotes(host, [{name: 'X', paras: []}]), 0);
// Kept across the Stripe round trip for its own order only, then taken once.
keepOrderNotes(NOTES, 'INV-2');
assert.deepStrictEqual(takeOrderNotes('INV-1'), []);           // another order's return
assert.deepStrictEqual(takeOrderNotes(null), []);
assert.deepStrictEqual(takeOrderNotes('INV-2'), NOTES);
assert.deepStrictEqual(takeOrderNotes('INV-2'), []);
keepOrderNotes(NOTES, 'INV-3'); keepOrderNotes([], 'INV-4');   // a later order with no note
assert.deepStrictEqual(takeOrderNotes('INV-3'), []);
keepOrderNotes(NOTES, '');
assert.deepStrictEqual(takeOrderNotes(''), []);
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_helper_renders_text_hides_when_empty_and_survives_the_round_trip(tmp_path):
    notes = [{"name": "Microbiome", "paras": [S1, S2]}]
    js = tmp_path / "n.js"
    js.write_text(JS.replace("SRC", _read("static/js/order-notes.js"))
                  .replace("NOTES_JSON", json.dumps(notes)))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
