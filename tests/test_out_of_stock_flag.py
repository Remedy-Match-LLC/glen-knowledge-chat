"""An out-of-stock product keeps its page and cannot be bought (Glen, 2026-10-16).

Glen: "the shelf is empty now" and "mark it out of stock". Vitamin D Syntropy's new
formula goes in stock on Monday, 19 October. A product with a non-empty `out_of_stock`
string is refused by the same predicate as `waitlist_only`
(practitioner_portal.waitlist_block), so every buying path that refuses a waiting-list
product refuses it too. The buyer reads one line, "{name} is out of stock. {note}", on
the page and in every refusal. Removing the field puts it back on sale.
"""
import json
import os
import re
import sqlite3

import pytest

import app
from dashboard import cart_store as CS
from dashboard import practitioner_portal as pp

VD = "vitamin-d-syntropy"
LINE = "Vitamin D Syntropy is out of stock. The new formula arrives Monday, 19 October."
URL = "/begin/product/vitamin-d-syntropy"
BUNDLE = "test-vd-bundle"
PAGE = open(os.path.join(os.path.dirname(__file__), "..", "static",
                         "begin-product.html")).read()


def _real():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    products = app._PRODUCTS.setdefault("products", {})
    real = _real()
    for s in (VD, "scar-silk"):
        monkeypatch.setitem(products, s, dict(real[s]))
    monkeypatch.setitem(products, BUNDLE, {
        "name": "Test VD Bundle", "bundle": True, "price_cents": 14000,
        "bundle_component_slugs": [{"slug": "scar-silk", "qty": 1}, {"slug": VD, "qty": 1}]})
    monkeypatch.setattr(pp.pricing, "_load_catalog", lambda: products)
    return products


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.fixture()
def logdb(monkeypatch, tmp_path):
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    cx.close()
    return path


def _refused(r):
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert body.get("error") == LINE, body
    assert body.get("url") == URL, body


def test_the_catalog_file_marks_it_out_of_stock_and_nothing_else():
    p = _real()[VD]
    assert p["out_of_stock"] == "The new formula arrives Monday, 19 October."
    assert not p.get("inactive") and not p.get("waitlist_only")
    assert [s for s, q in _real().items() if isinstance(q, dict) and q.get("out_of_stock")] == [VD]


def test_the_predicate_refuses_it_with_the_one_line(catalog):
    assert pp.is_orderable(VD, catalog) is False
    b = pp.waitlist_block(VD, catalog)
    assert b["slug"] == VD
    msg = pp.waitlist_refusal(b)
    assert msg == LINE and msg.url == URL


def test_a_bundle_holding_it_is_refused_and_names_it(catalog):
    assert pp.is_orderable(BUNDLE, catalog) is False
    assert pp.waitlist_refusal(pp.waitlist_block(BUNDLE, catalog)) == LINE


@pytest.mark.parametrize("value", ["", "   ", None, False])
def test_an_empty_flag_leaves_it_on_sale(catalog, monkeypatch, value):
    monkeypatch.setitem(catalog, VD, dict(catalog[VD], out_of_stock=value))
    assert pp.is_orderable(VD, catalog) is True
    assert pp.is_orderable(BUNDLE, catalog) is True


def test_a_waitlist_only_product_keeps_its_own_refusal(catalog, monkeypatch):
    monkeypatch.setitem(catalog, VD, dict(catalog[VD], waitlist_only=True))
    assert pp.waitlist_refusal(pp.waitlist_block(VD, catalog)).startswith(
        "Vitamin D Syntropy is not ready to order yet.")


def test_the_page_data_keeps_the_page_and_drops_everything_that_sells(client):
    r = client.get(f"/begin/product-page-data/{VD}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["out_of_stock_line"] == LINE
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d and "qty_pricing" not in d and d["autoship_eligible"] is False
    assert not any(isinstance(s, dict) and s.get("id") == "cta" for s in d.get("sections") or [])
    assert "waitlist_only" not in d and "waitlist" not in d


def test_an_ordinary_page_carries_no_line(client):
    d = client.get("/begin/product-page-data/scar-silk").get_json()
    assert "out_of_stock_line" not in d and d["price_cents"]


def test_the_buy_page_sends_the_buyer_to_the_product_page(client):
    r = client.get(f"/begin/buy/{VD}")
    assert r.status_code == 302 and r.headers["Location"].endswith(URL)


def test_begin_checkout_is_refused_before_any_payment(client, monkeypatch):
    calls = []
    for name in ("_stripe_checkout_url_for_reorder", "_stripe_checkout_url_for_order",
                 "_ingest_order"):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, lambda *a, _n=name, **k: calls.append(_n) or "")
    _refused(client.post(f"/begin/checkout/{VD}", json={"email": "a@x.com", "method": "card"}))
    assert calls == []


@pytest.mark.parametrize("slug", [VD, BUNDLE])
def test_cart_add_is_refused(client, logdb, slug):
    _refused(client.post("/api/cart/add", json={"slug": slug}))


def test_price_cart_refuses_it():
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": "scar-silk", "qty": 1}, {"slug": VD, "qty": 1}],
                        ship={"country": "US"})
    assert str(e.value) == LINE


def test_a_console_order_line_is_refused_too():
    assert app._cart_waitlist_refusal([{"slug": VD, "qty": 1}]) == LINE


def test_the_page_shows_the_line_and_points_every_buy_link_at_it():
    assert 'id="product-out-of-stock"' in PAGE
    assert re.search(r"data\.out_of_stock_line \? '#product-out-of-stock'", PAGE)
    assert "oos.textContent = data.out_of_stock_line || '';" in PAGE
    assert "if (!data.waitlist_only && !data.out_of_stock_line) { renderCartControl(slug); }" in PAGE


def test_a_bundle_page_holding_it_sells_nothing_and_names_it(client):
    """Review round 2: the bundle page kept its price and buy links."""
    d = client.get(f"/begin/product-page-data/{BUNDLE}").get_json()
    assert d["out_of_stock_line"] == LINE
    assert d["price"] == "" and d["price_cents"] is None and "cta_url" not in d


def test_a_bundle_with_a_waitlist_only_component_keeps_its_page(client, catalog, monkeypatch):
    monkeypatch.setitem(catalog, VD, dict(catalog[VD], waitlist_only=True))
    d = client.get(f"/begin/product-page-data/{BUNDLE}").get_json()
    assert "out_of_stock_line" not in d and d["price_cents"]


@pytest.mark.parametrize("value", [True, 1])
def test_a_true_flag_blocks_too_with_the_bare_line(catalog, monkeypatch, value):
    """Review round 1: `true`, written like waitlist_only, left it on sale."""
    monkeypatch.setitem(catalog, VD, dict(catalog[VD], out_of_stock=value))
    assert pp.is_orderable(VD, catalog) is False
    assert pp.waitlist_refusal(pp.waitlist_block(VD, catalog)) == "Vitamin D Syntropy is out of stock."


def test_a_recommended_line_carries_the_out_of_stock_line_not_not_ready():
    """Review round 1: the biofield preview and portal reorder said "not ready to order"."""
    keep, nr = app._split_not_ready([{"slug": "scar-silk"}, {"slug": VD}])
    assert keep == [{"slug": "scar-silk"}]
    assert nr == [{"name": "Vitamin D Syntropy", "url": URL, "line": LINE}]


def test_the_two_client_pages_print_the_line_when_it_is_given():
    root = os.path.join(os.path.dirname(__file__), "..", "static")
    bio = open(os.path.join(root, "begin-biofield.html")).read()
    portal = open(os.path.join(root, "client-portal.html")).read()
    assert "if (n.line) return '<div class=\"r-line\"><span><a href=\"'+esc(n.url)+'\">'+esc(n.line)+'</a>" in bio
    assert '${esc(it.not_ready.line || "Not ready to order yet")}' in portal
