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
NOTE = "The new formula arrives Monday, 19 October."
LINE = "Vitamin D Syntropy is out of stock. " + NOTE
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
    # The real record is back on sale (spec 2026-10-16-vitamin-d-syntropy), so the
    # refusal tests give their copy of it the flag the live file carried until then.
    monkeypatch.setitem(products, VD, dict(real[VD], out_of_stock=NOTE))
    monkeypatch.setitem(products, "scar-silk", dict(real["scar-silk"]))
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


def test_the_catalog_file_marks_only_night_vision_and_seaamino_out_of_stock():
    """Vitamin D Syntropy's new formula is back in stock (spec 2026-10-16-vitamin-d-syntropy)."""
    p = _real()[VD]
    assert "out_of_stock" not in p
    assert not p.get("inactive") and not p.get("waitlist_only")
    assert [s for s, q in _real().items()
            if isinstance(q, dict) and "out_of_stock" in q] == ["seaamino-syntropy",
                                                                 "night-vision"]


def test_night_vision_is_out_of_stock_with_no_date(client):
    """Glen, 2026-10-25, in the formulation tab: "Zero on shelf", and "yes" to marking it
    out of stock until the next batch. No restock date is known, so the line names none."""
    nv = _real()["night-vision"]
    assert nv["out_of_stock"] is True
    products = app._PRODUCTS["products"]
    assert pp.is_orderable("night-vision", products) is False
    line = "Night Vision is out of stock."
    assert pp.waitlist_refusal(pp.waitlist_block("night-vision", products)) == line
    d = client.get("/begin/product-page-data/night-vision").get_json()
    assert d["out_of_stock_line"] == line
    assert d["price_cents"] is None and "cta_url" not in d


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


def test_the_chat_is_told_night_vision_is_out_of_stock():
    """Unpinned, Night Vision brought no facts block, so the bot answered from old store
    snippets (review round 1, 2026-10-25). It now gets the refusal line and no dose."""
    b = app._named_product_facts("Can I order Night Vision?")
    assert "### Night Vision" in b
    assert "Availability: Night Vision is out of stock." in b
    assert "Give none, and do not take one from the snippets." in b
    assert "Night Vision cannot be ordered now. Never say it is available to order or in stock." in b


def test_only_the_unavailable_product_gets_the_line(monkeypatch):
    # The catalog fixture flags Vitamin D Syntropy; drop that so only Night Vision is out.
    real = _real()
    monkeypatch.setitem(app._PRODUCTS["products"], VD, dict(real[VD]))
    b = app._named_product_facts("Can I order Night Vision and Vitamin D Syntropy?")
    assert "\nNight Vision cannot be ordered now." in b
    assert "Vitamin D Syntropy cannot be ordered now." not in b
    assert b.count("cannot be ordered now.") == 1


def test_a_bundle_holding_it_carries_its_own_name(catalog, monkeypatch):
    monkeypatch.setitem(catalog, BUNDLE, dict(catalog[BUNDLE], copy_pinned=["intro"],
                                              intro="A test bundle."))
    b = app._named_product_facts("Can I order the Test VD Bundle?")
    assert "\nTest VD Bundle cannot be ordered now." in b


def test_the_product_table_gives_no_price_for_it():
    hint = app._alias_price_hint("Night Vision", "night-vision")
    assert "$" not in hint and "cannot be ordered now: Night Vision is out of stock." in hint
    assert app._product_guidance_hint("night-vision") == ""
    assert app._alias_price_hint("Scar Silk", "scar-silk").startswith(" — $")


def test_seaamino_is_out_of_stock_with_no_date(client):
    """Glen, 2026-10-25, formulation tab: "zero in stock", then "yes" to marking it out of
    stock as with Night Vision. No restock date is known."""
    assert _real()["seaamino-syntropy"]["out_of_stock"] is True
    products = app._PRODUCTS["products"]
    assert pp.is_orderable("seaamino-syntropy", products) is False
    line = "SeaAmino Syntropy is out of stock."
    assert pp.waitlist_refusal(pp.waitlist_block("seaamino-syntropy", products)) == line
    d = client.get("/begin/product-page-data/seaamino-syntropy").get_json()
    assert d["out_of_stock_line"] == line
    assert d["price_cents"] is None and "cta_url" not in d


def test_the_chat_is_told_seaamino_is_out_of_stock():
    b = app._named_product_facts("Can I order SeaAmino Syntropy?")
    assert "### SeaAmino Syntropy" in b
    assert "Availability: SeaAmino Syntropy is out of stock." in b
    assert "SeaAmino Syntropy cannot be ordered now." in b
    assert "$" not in app._alias_price_hint("SeaAmino Syntropy", "seaamino-syntropy")
