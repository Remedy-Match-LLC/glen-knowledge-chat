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


def _enable(monkeypatch):
    """/begin/cart needs BOTH the general cart flag and its own dark-launch
    flag (fix wave item 6). Most tests want the page reachable, so they call
    this instead of setting only one and getting a confusing 404."""
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_CART_PAGE_ENABLED", True)


def test_cart_page_follows_the_cart_flag(client, monkeypatch):
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", False)
    monkeypatch.setattr(app, "_CART_PAGE_ENABLED", True)
    assert client.get("/begin/cart").status_code == 404
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    for marker in ('id="cart-items"', 'id="cart-checkout"', "/api/cart/set-qty",
                   "/api/cart/checkout", "/static/optin-gate.js", "need_optin", "stripe_url"):
        assert marker in html
    assert "—" not in html


def test_cart_page_needs_its_own_dark_launch_flag_too(client, monkeypatch):
    """Fix wave item 6: _PORTAL_CART_ENABLED alone is not enough. A Stripe
    cancel currently strands the cart and misroutes the buyer, so /begin/cart
    stays 404 until _CART_PAGE_ENABLED is ALSO on, even with the general cart
    flag on."""
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(app, "_CART_PAGE_ENABLED", False)
    assert client.get("/begin/cart").status_code == 404


def test_cart_page_browse_link_points_at_match_not_shop(client, monkeypatch):
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert 'var BROWSE_PAGE = "/begin/match";' in html
    assert 'href="/shop"' not in html


def test_cart_page_has_accessibility_hooks(client, monkeypatch):
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert 'role="status"' in html
    assert 'aria-label="One fewer' in html
    assert 'aria-label="One more' in html
    assert ":focus-visible" in html


def test_cart_page_shows_no_price(client, monkeypatch):
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "confirmed at checkout" in html


def test_cart_page_mounts_the_theme_toggle(client, monkeypatch):
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "RMTheme.mountToggle(document.getElementById('themeToggle'))" in html


def test_cart_page_sets_the_session_cookie(client, monkeypatch):
    """Fix wave item 4: begin_product_page and begin_buy_page both set
    amg_session; begin_cart_page did not, though the opt-in step it triggers
    may need it. Matches those routes' exact pattern (only set when absent)."""
    _enable(monkeypatch)
    r = client.get("/begin/cart")
    assert r.status_code == 200
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "amg_session=" in set_cookie


def test_cart_page_does_not_reset_an_existing_session_cookie(client, monkeypatch):
    _enable(monkeypatch)
    client.set_cookie("amg_session", "already-set")
    r = client.get("/begin/cart")
    assert "amg_session=" not in r.headers.get("Set-Cookie", "")


def test_cart_page_has_a_shipping_address_form(client, monkeypatch):
    """Fix wave item 1 (CRITICAL): a first-time buyer has no saved order and
    no household ship-to, so /api/cart/checkout 400s with "Please add a
    shipping address to check out." unless the page can collect one. The form
    must carry the same keys _resolve_ship_address/checkout accept, and the
    page must post it as `address`."""
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    for field_id in ("ship-name", "ship-street", "ship-street2",
                      "ship-city", "ship-state", "ship-zip"):
        assert f'id="{field_id}"' in html
        # a real <label for=...>, not just a placeholder
        assert f'for="{field_id}"' in html
    assert "US addresses only" in html
    assert "body.address = addr;" in html
    assert "collectShipAddress" in html


def test_cart_page_form_starts_collapsed_behind_a_toggle(client, monkeypatch):
    """So a returning buyer with a saved address still checks out in one
    click: the form must not be visible by default."""
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert '<div id="ship-form" class="ship-form" hidden>' in html
    assert "Ship to a different address" in html


def test_cart_page_opens_and_focuses_the_form_on_the_address_error(client, monkeypatch):
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "isAddressError" in html
    assert "openShipForm" in html
    assert "shipFields.name.focus()" in html


def test_cart_page_shows_a_fixed_message_for_5xx(client, monkeypatch):
    """Fix wave item 2 (IMPORTANT): the server's 500 body is
    f"{type(e).__name__}: {e}" -- never fit for a customer. Only a 4xx error
    string is shown as is."""
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "status >= 500" in html
    assert "Checkout did not start. Please try again." in html


def test_cart_page_qty_and_remove_requests_handle_failure(client, monkeypatch):
    """Fix wave item 2, deferred minor: set-qty/remove share one handler
    (the remove button posts qty: 0 through it). A failed request must not
    leave the button silently doing nothing -- show a message and reload
    from the server so the displayed cart cannot drift from the real one."""
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert "/api/cart/set-qty" in html
    assert ".catch(function(){ msg.textContent" in html


def test_cart_page_handles_the_cancelled_checkout_query(client, monkeypatch):
    """Fix wave item 7: a buyer sent back from Stripe with
    ?checkout=cancelled sees a message once, in the status line, and the
    query is cleared so a refresh does not repeat it."""
    _enable(monkeypatch)
    html = client.get("/begin/cart").get_data(as_text=True)
    assert 'qparams.get("checkout") === "cancelled"' in html
    assert "Checkout was cancelled. Your cart is still here." in html
    assert "Checkout was cancelled." in html
    assert "history.replaceState" in html
