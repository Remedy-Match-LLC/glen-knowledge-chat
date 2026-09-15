"""Public store routes: the products API, /shop and /begin/cart."""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import pytest

import app

CAT = {
    "microbiome": {"name": "Microbiome", "price_cents": 6997, "description": "flora"},
    "fiber-cleanse": {"name": "Fiber Cleanse", "price_cents": 6997, "description": "fiber"},
    "info-page": {"name": "Info Page", "info_only": True},
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(app, "_get_product",
                        lambda s: dict(CAT[s], slug=s) if s in CAT else None)
    monkeypatch.setattr(app, "_PRODUCTS", {"products": CAT})
    monkeypatch.setattr(app, "_shop_programs", lambda: [
        {"condition_key": "symptom-digestion", "label": "Digestive Discomfort / Bloating",
         "items": [{"slug": "microbiome"}, {"slug": "fiber-cleanse"}]}])
    return app.app.test_client()


def test_products_api_is_dark_when_the_flag_is_off(client, monkeypatch):
    monkeypatch.setattr(app, "_SHOP_ENABLED", False)
    assert client.get("/api/shop/products").status_code == 404


def test_products_api_lists_searches_and_groups(client, monkeypatch):
    monkeypatch.setattr(app, "_SHOP_ENABLED", True)
    d = client.get("/api/shop/products").get_json()
    assert [p["slug"] for p in d["products"]] == ["fiber-cleanse", "microbiome"]
    assert d["groups"] == [{"key": "symptom-digestion",
                            "label": "Digestive Discomfort / Bloating", "count": 2}]
    d = client.get("/api/shop/products?q=flora").get_json()
    assert [p["slug"] for p in d["products"]] == ["microbiome"]


def test_products_api_filters_to_one_group_and_ignores_an_unknown_group(client, monkeypatch):
    monkeypatch.setattr(app, "_SHOP_ENABLED", True)
    d = client.get("/api/shop/products?group=symptom-digestion&q=fiber").get_json()
    assert [p["slug"] for p in d["products"]] == ["fiber-cleanse"]
    d = client.get("/api/shop/products?group=nope").get_json()
    assert d["products"] == []


def test_shop_page_is_dark_when_the_flag_is_off(client, monkeypatch):
    monkeypatch.setattr(app, "_SHOP_ENABLED", False)
    assert client.get("/shop").status_code == 404


def test_shop_page_serves_search_groups_grid_and_cart_link(client, monkeypatch):
    monkeypatch.setattr(app, "_SHOP_ENABLED", True)
    r = client.get("/shop")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for marker in ('id="shop-search"', 'id="shop-groups"', 'id="shop-grid"',
                   'href="/begin/cart"', "/api/shop/products"):
        assert marker in html
    assert "remedymatch.com" not in html
    assert "—" not in html  # no em dashes in customer copy


def test_shop_page_mounts_the_theme_toggle_like_the_product_page(client, monkeypatch):
    # Controller ruling: the cart page shipped without this once already, caught
    # in review. Match static/begin-product.html's markup and call exactly.
    monkeypatch.setattr(app, "_SHOP_ENABLED", True)
    html = client.get("/shop").get_data(as_text=True)
    assert 'id="theme-toggle-wrap"' in html
    assert 'id="themeToggle"' in html
    assert "RMTheme.mountToggle(document.getElementById('themeToggle'))" in html


def test_shop_page_gives_the_inline_link_a_focus_visible_style(client, monkeypatch):
    # Every card, chip and link needs a visible focus ring. The "Get matched to
    # a remedy" anchor is keyboard focusable, so it needs one too.
    monkeypatch.setattr(app, "_SHOP_ENABLED", True)
    html = client.get("/shop").get_data(as_text=True)
    assert ".text-link:focus-visible" in html
