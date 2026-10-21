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
