import os
# Dummy keys so `import app` (which constructs OpenAI + Pinecone clients at import)
# succeeds under a secretless CI without doppler.
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import pytest

import app


@pytest.fixture()
def client():
    return app.app.test_client()


def test_cart_page_follows_the_cart_flag(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", False)
    assert client.get("/begin/cart").status_code == 404
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    html = client.get("/begin/cart").get_data(as_text=True)
    for marker in ('id="cart-items"', 'id="cart-checkout"', "/api/cart/set-qty",
                   "/api/cart/checkout", "/static/optin-gate.js", "need_optin", "stripe_url"):
        assert marker in html
    assert "—" not in html


def test_cart_page_browse_link_points_at_match_not_shop(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert 'var BROWSE_PAGE = "/begin/match";' in html
    assert 'href="/shop"' not in html


def test_cart_page_has_accessibility_hooks(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert 'role="status"' in html
    assert 'aria-label="One fewer' in html
    assert 'aria-label="One more' in html
    assert ":focus-visible" in html


def test_cart_page_shows_no_price(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "confirmed at checkout" in html


def test_cart_page_mounts_the_theme_toggle(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "RMTheme.mountToggle(document.getElementById('themeToggle'))" in html
