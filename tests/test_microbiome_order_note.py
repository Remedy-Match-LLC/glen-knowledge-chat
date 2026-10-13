"""Microbiome's open-capsule note: product page, chatbot and buyer order email.

Glen approved the wording and the places in production's tab, 2026-10-13. Spec:
production/05 Formulations/_store-updates/2026-10-13-microbiome-note.md. The order note
is a per-product `order_note` field, so the next product needs no code.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S1 = "It is beneficial to open the capsule and take a small amount of the powder straight into your mouth."
S2 = "Then close the capsule and swallow it, so the rest of the powder reaches your intestines."
PRODUCTS = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
M = PRODUCTS["microbiome"]


def test_the_catalog_carries_the_approved_wording_exactly():
    assert M["directions"] == "1 capsule on rising.\n\n" + S1 + "\n\n" + S2
    assert M["order_note"] == S1 + "\n\n" + S2
    assert M["copy_pinned"] == ["directions"]
    assert "—" not in M["directions"] + M["order_note"]


def test_the_page_shows_each_sentence_as_its_own_paragraph():
    html = open(os.path.join(ROOT, "static", "begin-product.html"), encoding="utf-8").read()
    start = html.index("      // Suggested use, served straight from the catalog")
    end = html.index("      // Label line under the formula")
    js = """
    function el(tag){ return {tag: tag, kids: [], className: '', textContent: '',
      appendChild: function(c){ this.kids.push(c); }}; }
    var document = { createElement: el,
      createTextNode: function(t){ return {tag: '#text', textContent: t}; } };
    function run(d){ var wrapEl = el('div'); var body = {directions: d};
      %s
      return wrapEl.kids.map(function(p){ return p.kids.map(function(k){ return k.textContent; }).join(''); }); }
    console.log(JSON.stringify([run(%s), run("1 capsule daily.")]));
    """ % (html[start:end], json.dumps(M["directions"]))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    micro, single = json.loads(out.stdout)
    assert micro == ["Suggested use: 1 capsule on rising.", S1, S2]
    assert single == ["Suggested use: 1 capsule daily."]


def test_the_chatbot_states_the_directions_when_asked_about_microbiome():
    import app
    block = app.named_product_facts_block("How should I take Microbiome?")
    assert "### Microbiome" in block and S1 in block and S2 in block


def _gk(products):
    return {"reference": "ABC", "customer_firstname": "Pat",
            "products": [{"product_name": n, "product_quantity": q, "product_reference": "X"}
                         for n, q in products]}


def test_the_note_sits_under_its_own_product_in_the_buyer_email():
    import app
    from dashboard import groovekart_notify as G
    _s, body = G.buyer_email(_gk([("Golden Book", 1), ("Microbiome", 2), ("Clarity", 1)]),
                             note_for=app._order_note_for_name)
    lines = body.splitlines()
    i = lines.index("  2 x Microbiome")
    assert lines[i + 1:i + 6] == ["", "    " + S1, "", "    " + S2, ""]
    assert body.count(S1) == 1 and "  1 x Clarity" in lines[i + 6:]
    g = lines.index("  1 x Golden Book")
    assert lines[g + 1] == "  2 x Microbiome"


def test_a_failing_lookup_still_sends_the_confirmation():
    from dashboard import groovekart_notify as G

    def boom(name):
        raise RuntimeError("catalog down")
    _s, body = G.buyer_email(_gk([("Microbiome", 1)]), note_for=boom)
    assert "  1 x Microbiome" in body and S1 not in body


@pytest.mark.parametrize("name,expected", [
    ("Microbiome", S1 + "\n\n" + S2), (" microbiome ", S1 + "\n\n" + S2),
    ("Microbiome Support Kit", ""), ("Golden Book", ""), ("", ""), (None, "")])
def test_the_note_is_found_by_exact_name_only(name, expected):
    import app
    assert app._order_note_for_name(name) == expected


def test_a_retired_record_carries_no_note(monkeypatch):
    import app
    prods = json.loads(json.dumps(app._PRODUCTS))
    prods["products"]["microbiome"]["inactive"] = True
    monkeypatch.setattr(app, "_PRODUCTS", prods)
    assert app._order_note_for_name("Microbiome") == ""


def test_a_question_about_the_gut_microbiome_does_not_pull_in_the_product():
    import app
    assert "### Microbiome" not in app.named_product_facts_block(
        "how can I support my gut microbiome after antibiotics?")


def test_the_store_webhook_sends_the_buyer_the_note(monkeypatch):
    import app
    from dashboard import inbox
    sent = []
    monkeypatch.setattr(inbox, "send_email", lambda to, subj, body, **k: sent.append((to, body)))
    payload = _gk([("Microbiome", 1)])
    payload["customer_email"] = "buyer@example.com"
    app._notify_store_order(payload)
    buyer = [b for to, b in sent if to == "buyer@example.com"]
    assert buyer and S1 in buyer[0] and S2 in buyer[0]
    assert all(S1 not in b for to, b in sent if to != "buyer@example.com")


def _confirmation(monkeypatch, items):
    import app
    from dashboard import stripe_pay
    monkeypatch.setattr(stripe_pay, "get_session", lambda sid: {
        "payment_status": "paid", "amount_total": 7000, "customer_email": "b@example.com",
        "metadata": {"invoice_id": "INH-TEST", "kind": "store"}})
    monkeypatch.setattr(app._bos_orders, "find_order_by_external_ref",
                        lambda cx, ref: {"id": 9, "items": items, "total_cents": 7000})
    app.app.config["TESTING"] = True
    r = app.app.test_client().get("/order-confirmation?session_id=cs_test")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_the_payment_received_page_shows_the_note_under_microbiome(monkeypatch):
    page = _confirmation(monkeypatch, [{"slug": "golden-book", "name": "Golden Book", "qty": 1},
                                       {"slug": "microbiome", "name": "Microbiome", "qty": 2}])
    i = page.index("Microbiome")
    assert page.index('<p class="note">' + S1 + "</p>") > i
    assert page.index('<p class="note">' + S2 + "</p>") > page.index(S1)
    assert page.count('class="note"') == 2 and page.index("Golden Book") < i


def test_a_zero_quantity_line_gets_no_note(monkeypatch):
    page = _confirmation(monkeypatch, [{"slug": "microbiome", "name": "Microbiome", "qty": 0}])
    assert S1 not in page


@pytest.mark.parametrize("q", [
    "Microbiome health: how do I rebuild it after antibiotics?",
    "What does the Microbiome do for immunity?",
    "Gut Microbiome and eczema",
    "microbiome and mood"])
def test_a_general_microbiome_question_is_not_a_product_question(q):
    import app
    assert "### Microbiome" not in app.named_product_facts_block(q)


@pytest.mark.parametrize("q", ["How should I take Microbiome?", "how much microbiome per day?",
                               "Microbiome dose for a child", "Is Microbiome safe with warfarin?"])
def test_a_product_question_about_microbiome_still_gets_the_directions(q):
    import app
    assert S1 in app.named_product_facts_block(q)


def test_a_rebuilt_record_gets_the_three_fields_back():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "apply_enrichment", os.path.join(ROOT, "scripts", "apply_enrichment.py"))
    ae = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ae)
    corr = json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))
    bare = {k: v for k, v in M.items() if k not in ("directions", "copy_pinned", "order_note")}
    products = {"microbiome": bare}
    ae.apply_corrections(products, {"microbiome": corr["microbiome"]})
    got = products["microbiome"]
    assert (got["directions"], got["copy_pinned"], got["order_note"]) == (
        M["directions"], M["copy_pinned"], M["order_note"])
    assert got["ingredients_source"] == M["ingredients_source"]


@pytest.mark.parametrize("q", ["what is microbiome?", "what is microbiome and why does it matter",
                               "What's Microbiome?"])
def test_asking_what_microbiome_is_is_not_a_product_question(q):
    import app
    assert "### Microbiome" not in app.named_product_facts_block(q)


@pytest.mark.parametrize("q", ["How much reverse age per year can I expect?",
                               "take clear the way per se", "what is appestat per se"])
def test_per_does_not_widen_other_product_names(q):
    import app
    assert "###" not in app.named_product_facts_block(q)


def test_the_note_on_the_confirmation_page_is_escaped(monkeypatch):
    import app
    prods = json.loads(json.dumps(app._PRODUCTS))
    prods["products"]["microbiome"]["order_note"] = "<b>bold</b>"
    monkeypatch.setattr(app, "_PRODUCTS", prods)
    page = _confirmation(monkeypatch, [{"slug": "microbiome", "name": "Microbiome", "qty": 1}])
    assert "&lt;b&gt;bold&lt;/b&gt;" in page and "<b>bold</b>" not in page


# --- Zelle buyers: a separate card under the instructions (Glen, 2026-10-13) ----------

def test_order_notes_lists_each_bought_product_with_a_note_once():
    import app
    got = app._order_notes_for([
        {"slug": "golden-book", "qty": 1}, {"slug": "microbiome", "qty": 2},
        {"slug": "microbiome", "qty": 1}, {"slug": "immune-modulation", "qty": 0}, "junk"])
    assert got == [{"name": "Microbiome", "paras": [S1, S2]}]
    assert app._order_notes_for([{"slug": "microbiome", "qty": 0}]) == []


def test_the_cart_zelle_reply_carries_the_note(monkeypatch, tmp_path):
    import sqlite3
    import app
    import dashboard.orders as O
    from dashboard import cart_store as CS, qbo_billing
    path = str(tmp_path / "log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    O.init_orders_table(cx)
    cx.commit()
    cx.close()
    monkeypatch.setattr(app, "is_member", lambda sid, email: True)
    monkeypatch.setattr(app, "_cart_email", lambda: "z@x.com")
    monkeypatch.setattr(app, "_get_product", lambda s: {
        "slug": s, "name": "Microbiome", "price_cents": 7000, "qty_pricing": True,
        "qbo_item_id": "29"} if s == "microbiome" else None)
    monkeypatch.setattr(app._shipping, "quote", lambda b: {"shipping_cents": 1265})
    monkeypatch.setattr(qbo_billing, "create_invoice",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no QBO")))
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    c = app.app.test_client()
    c.post("/api/cart/add", json={"slug": "microbiome"})
    d = c.post("/api/cart/checkout", json={
        "address": {"name": "A B", "street": "1 Main", "city": "Hilo", "state": "HI",
                    "zip": "96720", "country": "US"},
        "method": "zelle", "checkout_request_id": "c" * 32}).get_json()
    assert d["method"] == "zelle"
    assert d["order_notes"] == [{"name": "Microbiome", "paras": [S1, S2]}]
    again = c.post("/api/cart/zelle-orders", json={"refs": ["c" * 32]}).get_json()["orders"]
    assert again[0]["order_notes"] == d["order_notes"]


def _node(js):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _fn(path, name):
    src = open(os.path.join(ROOT, *path.split("/")), encoding="utf-8").read()
    i = src.index("function %s(" % name)
    d, j = 0, src.index("{", i)
    while True:
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            return src[i:j + 1]
        j += 1


NOTES = [{"name": "Microbiome", "paras": [S1, S2]}]
ESC = ("function esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,"
       "function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});}"
       "function money(c){return '$'+(Number(c||0)/100).toFixed(2);}")


@pytest.mark.parametrize("page,box", [("static/begin-cart.html", "zelleHtml"),
                                      ("static/client-portal.html", "zelleInstructionsHtml")])
def test_the_note_is_its_own_card_after_the_zelle_box(page, box):
    # Each page's own esc, so a broken escape fails here (review round 2).
    src = open(os.path.join(ROOT, *page.split("/")), encoding="utf-8").read()
    esc = (_fn(page, "esc") if "function esc(" in src
           else next(l for l in src.splitlines() if l.startswith("const esc =")))
    money = "function money(c){return '$'+(Number(c||0)/100).toFixed(2);}"
    js = esc + money + _fn(page, "orderNotesHtml") + _fn(page, box) + (
        "console.log(JSON.stringify([%s({order_id:5,total_cents:100,pay_instructions:{to:'x'},"
        "order_notes:%s}), %s({order_id:5,pay_instructions:{}}),"
        "orderNotesHtml([{name:'<i>x</i>',paras:['a<b']}])]));" % (box, json.dumps(NOTES), box))
    with_note, without, hostile = _node(js)
    assert "<i>" not in hostile and "&lt;i&gt;x&lt;/i&gt;" in hostile and "a&lt;b" in hostile
    card = with_note.index("order-notes")
    assert with_note.rindex("</div>", 0, card) > with_note.index("Zelle payment instructions")
    assert "<p><strong>Microbiome</strong></p><p>" + S1 + "</p><p>" + S2 + "</p>" in with_note
    assert "order-notes" not in without


def test_the_portal_basket_zelle_reply_carries_the_note(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod, email="zelle-micro@example.com", content={
        "greeting": "hi", "video": {}, "layers": [],
        "reorder_items": [{"slug": "microbiome", "qty": 1}]})
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: 77)
    r = c.post(f"/api/portal/{tok}/checkout", json={
        "method": "zelle", "checkout_request_id": "zelle_micro_12345678",
        "items": [{"slug": "microbiome", "qty": 1}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["order_notes"] == [{"name": "Microbiome", "paras": [S1, S2]}]


from tests.test_portal_item_reorder import client, _seed_portal  # noqa: E402,F401  fixtures
