"""No surface offers a not-ready product for sale (Alkalize, 2026-10-20).

Glen's ruling for Alkalize Bicarbonate Blend: "waiting list, no orders". Checkout already
refused every waitlist_only product. Five places still showed a price, a Buy button or an
Add button, or took payment. Each is closed with the one predicate (_waitlist_block):

  a. /api/invoice/<token>/pay refuses an invoice holding one, before any charge.
  b. _resolve_complement never suggests one (concierge and portal chat "Add to my order").
  c. The learn page shows no price and no Order button, and links to the product page.
  d. A related-product card shows no price for one.
  e. /api/reorder/items marks one unavailable, with the refusal line.

Each test reads the real records from data/products.json, and has an orderable control.
"""
import importlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import app
from dashboard import practitioner_portal as pp

ALK = "alkalize-bicarbonate-blend"
WAITLIST = ("alkalize-bicarbonate-blend", "scar-soft-drink", "fungal-nail-repair")
CONTROL = "magnesium-syntropy"
ALK_REFUSAL = ("Alkalize Bicarbonate Blend is not ready to order yet. "
               "Join the waiting list on its page.")


def _real():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    products = app._PRODUCTS.setdefault("products", {})
    real = _real()
    for s in (*WAITLIST, CONTROL):
        monkeypatch.setitem(products, s, dict(real[s]))
    monkeypatch.setattr(pp.pricing, "_load_catalog", lambda: products)
    return products


# ── a. Paying an invoice ──────────────────────────────────────────────────────

@pytest.fixture
def inv(tmp_path, monkeypatch):
    db = str(tmp_path / "pay.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(pp, "_LOG_DB", Path(db))
    monkeypatch.setattr(app, "INVOICE_PAYLINK_ENABLED", True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    from dashboard import stripe_pay
    made = []
    monkeypatch.setattr(stripe_pay, "create_checkout_session",
                        lambda amount, **k: made.append(amount) or {"url": "https://stripe.test/s"})
    from dashboard import orders as O, order_payments as OP
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    OP.ensure_table(cx)

    def order(ref, items):
        oid = O.upsert_order(cx, source="dropship", external_ref=ref, email="t@example.com",
                             items=items, total_cents=7000, status="proposed")
        cx.commit()
        return pp.create_order_invoice_token(oid, db_path=db)

    return {"c": app.app.test_client(), "order": order, "made": made}


@pytest.mark.parametrize("slug", WAITLIST)
@pytest.mark.parametrize("method", ["card", "zelle"])
def test_an_invoice_holding_a_not_ready_product_cannot_be_paid(inv, slug, method):
    tok = inv["order"](f"DS-{slug}", [{"slug": CONTROL, "qty": 1}, {"slug": slug, "qty": 1}])
    r = inv["c"].post(f"/api/invoice/{tok}/pay", json={"method": method})
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert "is not ready to order yet" in body["error"]
    assert body["url"] == f"/begin/product/{slug}"
    assert "instructions" not in body and inv["made"] == []


def test_an_ordinary_invoice_is_still_paid(inv):
    tok = inv["order"]("DS-control", [{"slug": CONTROL, "qty": 1}])
    r = inv["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 200, r.get_json()
    assert inv["made"] == [7000]


# ── b. Concierge suggestions ──────────────────────────────────────────────────

class _Idx:
    def __init__(self, title):
        self.title = title

    def query(self, **k):
        m = type("M", (), {"score": 0.99, "metadata": {"title": self.title, "price": "$70",
                                                      "url": "https://x.test/p"}})()
        return type("R", (), {"matches": [m]})()


@pytest.mark.parametrize("slug,ok", [(ALK, False), (CONTROL, True)])
def test_the_concierge_never_suggests_a_not_ready_product(monkeypatch, slug, ok):
    title = "Alkalize Bicarbonate Blend Title" if slug == ALK else "Magnesium Control Title"
    monkeypatch.setitem(app._TITLE_TO_SLUG, title, slug)
    monkeypatch.setattr(app, "embed", lambda _n: [0.0])
    monkeypatch.setattr(app, "_idx", _Idx(title))
    app._COMPLEMENT_CACHE.clear()
    try:
        c = app._resolve_complement(title)
    finally:
        app._COMPLEMENT_CACHE.clear()
    if ok:
        assert c["in_catalog"] and c["slug"] == CONTROL
    else:
        assert not c["in_catalog"] and not c["url"] and not c["price"] and c["slug"] is None


# ── c. The learn page ─────────────────────────────────────────────────────────

def test_the_learn_page_has_no_price_or_order_button(monkeypatch):
    monkeypatch.setattr(app, "_product_content", None)
    d = app.app.test_client().get(f"/begin/learn-data/{ALK}").get_json()
    assert d["price"] == ""
    assert d["buy_url"] == f"/begin/product/{ALK}"
    assert d["not_ready_line"] == ALK_REFUSAL


def test_an_ordinary_learn_page_keeps_its_price(monkeypatch):
    monkeypatch.setattr(app, "_product_content", None)
    d = app.app.test_client().get(f"/begin/learn-data/{CONTROL}").get_json()
    assert d["price"].startswith("$") and d["buy_url"] == f"/begin/buy/{CONTROL}"
    assert "not_ready_line" not in d


def test_the_learn_page_script_hides_the_order_buttons_for_the_line():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static",
                             "begin-learn.html")).read()
    i = html.index("if (d.not_ready_line){")
    branch = html[i:html.index("} else if (d.info_only){", i)]
    assert "hide('buy-top')" in branch and "hide('h-price')" in branch
    assert "d.not_ready_line" in branch and "d.buy_url" in branch


# ── d. Related-product cards ──────────────────────────────────────────────────

def test_a_related_card_shows_no_price_for_a_not_ready_product(monkeypatch):
    from dashboard import related_products as rp, related_store as rstore
    monkeypatch.setattr(app, "_RELATED_PRODUCTS_ENABLED", True)
    monkeypatch.setattr(rstore, "load_manual", lambda s: [ALK, CONTROL])
    monkeypatch.setattr(rstore, "load_harvested", lambda s: [])
    monkeypatch.setattr(app, "_related_semantic", lambda s, k=12: [])
    base = "scar-soft-drink"
    d = app.app.test_client().get(f"/begin/product-page-data/{base}").get_json()
    sec = next(s for s in d["sections"] if s["id"] == "related")
    cards = {c["slug"]: c for c in sec["body"]["featured"] + sec["body"]["more"]}
    assert cards[ALK]["price"] == "" and cards[ALK]["url"] == f"/begin/product/{ALK}"
    assert cards[CONTROL]["price"].startswith("$")


# ── e. Reorder list ───────────────────────────────────────────────────────────

def test_the_reorder_list_marks_it_unavailable_with_the_line(monkeypatch, tmp_path):
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "_reorder_email_from_cookie", lambda: "r@x.com")
    monkeypatch.setattr(app, "is_member", lambda *a, **k: True)
    monkeypatch.setattr(app, "_REVIEWS_ENABLED", False)
    monkeypatch.setattr(app._bos_orders, "list_orders_by_email", lambda cx, email: [
        {"created_at": "2026-09-01", "items": [
            {"name": "Alkalize Bicarbonate Blend", "qty": 1},
            {"name": "Magnesium Syntropy", "qty": 1}]}])
    monkeypatch.setattr(app, "_resolve_buy_slug",
                        lambda nm: {"Alkalize Bicarbonate Blend": ALK,
                                    "Magnesium Syntropy": CONTROL}.get(nm))
    items = {i["name"]: i for i in
             app.app.test_client().get("/api/reorder/items").get_json()["items"]}
    alk = items["Alkalize Bicarbonate Blend"]
    assert alk["available"] is False and alk["not_ready_line"] == ALK_REFUSAL
    assert items["Magnesium Syntropy"]["available"] is True
    assert "not_ready_line" not in items["Magnesium Syntropy"]


def test_the_reorder_page_prints_the_line():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static", "reorder.html")).read()
    assert "esc(it.not_ready_line || 'no longer available')" in html


# ── Fix round 1 (review round 1, 2026-10-20) ──────────────────────────────────

def _order(cx, ref, items, **kw):
    from dashboard import orders as O
    oid = O.upsert_order(cx, source=kw.pop("source", "dropship"), external_ref=ref,
                         email=kw.pop("email", "t@example.com"), items=items,
                         total_cents=kw.pop("total", 7000), status=kw.pop("status", "proposed"))
    sets = ", ".join(f"{k}=?" for k in kw)
    if sets:
        cx.execute(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))
    cx.commit()
    return oid


# 1. Caregiver pay

@pytest.fixture
def care(tmp_path, monkeypatch):
    from dashboard import household as hh, orders as O, stripe_pay
    db = str(tmp_path / "care.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    monkeypatch.setattr(app, "_caregiver_pay_enabled", lambda: True)
    monkeypatch.setattr(app, "_STRIPE_ACTIVE", True)
    monkeypatch.setattr(app, "_portal_record_for", lambda cx, tok: {"email": "payer@x.com"})
    monkeypatch.setattr(hh, "can_pay", lambda cx, payer, ben: True)
    made = []
    monkeypatch.setattr(stripe_pay, "create_checkout_session",
                        lambda amount, **k: made.append(amount) or {"url": "https://stripe.test/c"})
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    return cx, made


@pytest.mark.parametrize("slug", WAITLIST)
def test_caregiver_pay_refuses_an_order_holding_a_not_ready_product(care, slug):
    cx, made = care
    oid = _order(cx, f"CG-{slug}", [{"slug": CONTROL, "qty": 1}, {"slug": slug, "qty": 1}],
                 status="open")
    r = app.app.test_client().post("/api/portal/tok/caregiver-pay", json={"order_id": oid})
    assert r.status_code == 400, r.get_json()
    assert "is not ready to order yet" in r.get_json()["error"]
    assert r.get_json()["url"] == f"/begin/product/{slug}"
    assert made == []


def test_caregiver_pay_still_pays_an_ordinary_order(care):
    cx, made = care
    oid = _order(cx, "CG-control", [{"slug": CONTROL, "qty": 1}], status="open")
    r = app.app.test_client().post("/api/portal/tok/caregiver-pay", json={"order_id": oid})
    assert r.status_code == 200 and made == [7000], r.get_json()


# 2. Past orders do not freeze

def _like_paid_with_balance(inv_order, items):
    """Paid $70 on the order row, total since raised to $110: $40 owed."""
    return inv_order(items, total=11000, pay_status="paid", pay_method="card",
                     paid_cents=7000, paid_at="2026-10-01T00:00:00Z")


@pytest.fixture
def inv2(inv, tmp_path):
    cx = sqlite3.connect(app.LOG_DB)
    cx.row_factory = sqlite3.Row
    n = {"i": 0}

    def order(items, **kw):
        n["i"] += 1
        oid = _order(cx, f"DS-X{n['i']}", items, **kw)
        return pp.create_order_invoice_token(oid, db_path=app.LOG_DB)
    return dict(inv, order2=order)


def test_a_paid_order_holding_it_can_still_pay_its_balance(inv2):
    tok = _like_paid_with_balance(inv2["order2"], [{"slug": ALK, "qty": 1}, {"slug": CONTROL, "qty": 1}])
    o = inv2["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["payable"] is True and o["not_ready"] is None
    r = inv2["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 200, r.get_json()
    assert inv2["made"] == [4000]


def test_a_shipped_unpaid_order_holding_it_can_still_pay(inv2):
    tok = inv2["order2"]([{"slug": ALK, "qty": 1}], status="shipped")
    r = inv2["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 200, r.get_json()


def test_an_unpaid_invoice_holding_it_shows_the_line_not_pay(inv2):
    tok = inv2["order2"]([{"slug": ALK, "qty": 1}, {"slug": CONTROL, "qty": 1}])
    o = inv2["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["payable"] is False
    assert o["not_ready"] == {"line": ALK_REFUSAL, "url": f"/begin/product/{ALK}"}
    ok = inv2["c"].get(f"/api/invoice/{inv2['order2']([{'slug': CONTROL, 'qty': 1}])}").get_json()
    assert ok["order"]["payable"] is True and ok["order"]["not_ready"] is None


def test_the_invoice_page_shows_the_line_in_place_of_pay():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static", "invoice.html")).read()
    i = html.index("if (ORDER.not_ready){")
    assert i < html.index("if (!ORDER.payable)")
    branch = html[i:html.index("return;", i)]
    assert "'pay-btn'" in branch and "ORDER.not_ready.url" in branch and "ORDER.not_ready.line" in branch
    assert "if (j.url) $('pay-msg').innerHTML" in html


class _Owner:
    role = app._bos_rbac.OWNER
    email = "owner@x.com"


@pytest.fixture
def edit(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "e.db"))
    monkeypatch.setattr(app, "_bos_actor", lambda: _Owner())
    held = {"items": [{"slug": ALK, "qty": 1}, {"slug": CONTROL, "qty": 1}]}
    monkeypatch.setattr(app._bos_orders, "get_order", lambda cx, oid: {
        "id": oid, "status": "done", "external_ref": "X", "items": held["items"]})
    calls = []
    monkeypatch.setattr(app, "_reprice_and_persist_invoice",
                        lambda cx, order, lines, **k: calls.append((lines, k)) or (
                            {"items_rec": lines, **{f: 0 for f in (
                                "subtotal_cents", "discount_cents", "adjustment_cents",
                                "shipping_cents", "get_cents", "points_redeemed_cents",
                                "total_cents")}}, False))
    monkeypatch.setattr(app, "_push_invoice_edit_to_qbo", lambda *a, **k: None)
    monkeypatch.setattr(app, "_special_price_warning", lambda *a, **k: "")
    return app.app.test_client(), calls


def test_the_owner_can_keep_a_line_already_on_the_order(edit):
    c, calls = edit
    r = c.post("/api/orders/5/edit", json={"lines": [{"slug": ALK, "qty": 1},
                                                     {"slug": CONTROL, "qty": 2}]})
    assert r.status_code == 200, r.get_json()
    assert calls and calls[0][1]["allow_waitlist"] is True


@pytest.mark.parametrize("lines", [[{"slug": ALK, "qty": 2}],
                                   [{"slug": CONTROL, "qty": 1}, {"slug": "scar-soft-drink", "qty": 1}]])
def test_the_owner_cannot_raise_or_add_a_not_ready_line(edit, lines):
    c, calls = edit
    r = c.post("/api/orders/5/edit", json={"lines": lines})
    assert r.status_code == 400 and "is not ready to order yet" in r.get_json()["error"]
    assert calls == []


def test_a_kept_line_prices_only_when_the_caller_allows_it():
    lines = [{"slug": ALK, "qty": 1}]
    with pytest.raises(app.CheckoutError):
        app._price_inhouse_invoice(lines, email="", pickup=True, ship=None)
    assert app._price_inhouse_invoice(lines, email="", pickup=True, ship=None,
                                      allow_waitlist=True) is not None


@pytest.fixture
def cupd(monkeypatch):
    monkeypatch.setattr(app, "_invoice_order_for_token", lambda token: {
        "id": 1, "status": "proposed", "external_ref": "X",
        "items": [{"slug": ALK, "qty": 1}, {"slug": CONTROL, "qty": 1}]})
    calls = []
    monkeypatch.setattr(app, "_reprice_and_persist_invoice",
                        lambda cx, order, lines, **k: calls.append((lines, k)) or (
                            {"total_cents": 7000}, False))
    monkeypatch.setattr(app._bos_orders, "get_order", lambda cx, oid: {"id": 1, "items": []})
    monkeypatch.setattr(app, "_invoice_summary", lambda o: {})
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: None)
    return app.app.test_client(), calls


def test_the_client_can_remove_it(cupd, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "u.db"))
    c, calls = cupd
    r = c.post("/api/invoice/tok/update", json={"lines": [{"slug": CONTROL, "qty": 1}]})
    assert r.status_code == 200, r.get_json()
    assert [l["slug"] for l in calls[0][0]] == [CONTROL]


def test_the_client_cannot_raise_it(cupd):
    c, calls = cupd
    r = c.post("/api/invoice/tok/update", json={"lines": [{"slug": ALK, "qty": 2}]})
    assert r.status_code == 400 and r.get_json()["error"] == ALK_REFUSAL
    assert calls == []


# 3. Learn page: the waiting-list button only for a product with a list

def test_the_learn_page_offers_the_list_only_for_a_waitlist_product(monkeypatch, catalog):
    monkeypatch.setattr(app, "_product_content", None)
    vd = "vitamin-d-syntropy"
    monkeypatch.setitem(catalog, vd, dict(_real()[vd]))
    c = app.app.test_client()
    alk = c.get(f"/begin/learn-data/{ALK}").get_json()
    assert alk["waitlist"] is True
    v = c.get(f"/begin/learn-data/{vd}").get_json()
    assert v["waitlist"] is False and v["price"] == ""
    assert v["not_ready_line"].startswith("Vitamin D Syntropy is out of stock.")
    html = open(os.path.join(os.path.dirname(__file__), "..", "static",
                             "begin-learn.html")).read()
    i = html.index("if (d.waitlist){")
    assert "Join the waiting list" in html[i:html.index("} else {", i)]
    assert "nb.style.display = 'none'" in html[i:i + 400]


# 4. The invoice product picker

def test_the_invoice_picker_leaves_not_ready_products_out(monkeypatch, catalog):
    monkeypatch.setattr(app, "_invoice_order_for_token", lambda token: {"id": 1})
    monkeypatch.setattr(app._bos_products, "catalog", lambda **kw: [
        dict(catalog[s], slug=s) for s in (*WAITLIST, CONTROL)])
    slugs = [p["slug"] for p in
             app.app.test_client().get("/api/invoice/tok/products").get_json()["products"]]
    assert slugs == [CONTROL]


def test_the_reorder_row_links_the_line_to_its_page():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static", "reorder.html")).read()
    assert '<a href="${esc(it.not_ready_url)}">${esc(it.not_ready_line)}</a>' in html


# 5. Zelle store orders

@pytest.fixture
def zel(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "z.db"))
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_cart_email", lambda: "z@x.com")
    orders = {}
    monkeypatch.setattr(app._bos_orders, "find_order_by_external_ref",
                        lambda cx, r: orders.get(r))
    return app.app.test_client(), orders


def _zorder(ref, items, **kw):
    return {"id": 9, "external_ref": ref, "email": "z@x.com", "source": "reorder",
            "pay_method": "zelle", "pay_status": "unpaid", "status": "new",
            "items": items, "total_cents": 7000, "shipping_cents": 0, **kw}


def test_an_unpaid_zelle_order_holding_it_gets_the_line_not_instructions(zel):
    c, orders = zel
    a, b, p = "a" * 32, "b" * 32, "c" * 32
    orders[a] = _zorder(a, [{"slug": ALK, "qty": 1}])
    orders[b] = _zorder(b, [{"slug": CONTROL, "qty": 1}])
    orders[p] = _zorder(p, [{"slug": ALK, "qty": 1}], pay_status="paid")
    d = c.post("/api/cart/zelle-orders", json={"refs": [a, b, p]}).get_json()
    got = {o["order_ref"]: o for o in d["orders"]}
    assert "pay_instructions" not in got[a]
    assert got[a]["not_ready"] == {"line": ALK_REFUSAL, "url": f"/begin/product/{ALK}"}
    assert "pay_instructions" in got[b] and "not_ready" not in got[b]
    assert d["closed"] == [p]


def test_the_cart_page_prints_the_line_for_a_not_ready_zelle_order():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static", "begin-cart.html")).read()
    i = html.index("function zelleHtml(d){")
    head = html[i:i + 400]
    assert "if (d.not_ready){" in head and "d.not_ready.url" in head and "d.not_ready.line" in head


def test_a_part_paid_unpaid_order_holding_it_can_pay_the_rest(inv):
    from dashboard import order_payments as OP
    cx = sqlite3.connect(app.LOG_DB)
    cx.row_factory = sqlite3.Row
    oid = _order(cx, "DS-part", [{"slug": ALK, "qty": 1}], total=7000)
    OP._insert(cx, oid, kind="payment", amount_cents=3000, method="zelle", source="manual",
               external_ref="", refunds_payment_id=None, paid_at=None, note="", actor="t")
    tok = pp.create_order_invoice_token(oid, db_path=app.LOG_DB)
    o = inv["c"].get(f"/api/invoice/{tok}").get_json()["order"]
    assert o["payable"] is True and o["not_ready"] is None
    r = inv["c"].post(f"/api/invoice/{tok}/pay", json={"method": "card"})
    assert r.status_code == 200, r.get_json()
    assert inv["made"] == [4000]


@pytest.mark.parametrize("allow", [True, False])
def test_the_repricer_hands_the_kept_line_permission_to_the_pricer(monkeypatch, allow):
    seen = []
    monkeypatch.setattr(app, "_price_inhouse_invoice",
                        lambda lines, **k: seen.append(k) or None)
    app._reprice_and_persist_invoice(None, {"email": "", "items": []}, [{"slug": ALK, "qty": 1}],
                                     pickup=True, allow_waitlist=allow)
    assert seen and seen[0].get("allow_waitlist", False) is allow
    assert ("allow_waitlist" in seen[0]) is allow
