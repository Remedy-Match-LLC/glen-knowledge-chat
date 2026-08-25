from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "static" / "shell.js").read_text()
PORTAL = (ROOT / "static" / "client-portal.html").read_text()


def test_portal_shell_cart_sits_before_my_path_and_is_portal_only():
    cart = SHELL.index('js-portal-cart-btn')
    my_path_append = SHELL.index('bar.appendChild(mypathBtn)')
    assert cart < my_path_append
    assert '/^\\/portal\\/' in SHELL


def test_header_cart_opens_dedicated_portal_cart_not_embedded_checkout():
    assert 'window.openPortalCart(true)' in SHELL
    assert 'window.openPortalOrderBasket(true)' not in SHELL
    assert 'function openPortalCart(captureContext)' in PORTAL
    assert 'showTab("cart")' in PORTAL
    assert 'window.openPortalCart = openPortalCart' in PORTAL
    assert 'if (name === \'cart\') loadCart()' in PORTAL


def test_review_order_still_opens_embedded_checkout():
    assert 'openPortalOrderBasket(false)' in PORTAL
    assert 'showTab("current")' in PORTAL
    assert 'id="portal-order-basket"' in PORTAL
    assert 'document.getElementById("portal-order-basket")' in PORTAL


def test_legacy_portals_always_render_a_dedicated_cart_panel():
    assert 'actTiles.push(["cart", "My Cart"' in PORTAL
    assert '${_hub ? `<section data-panel="cart" hidden>' in PORTAL
    assert '_hub && v.cart && v.cart.enabled ? `<section data-panel="cart"' not in PORTAL


def test_header_cart_count_tracks_shared_remedy_basket():
    assert '#curatedOrderItems .curated-order-item' in PORTAL
    assert 'row.getAttribute("data-qty")' in PORTAL
    assert 'window.setPortalHeaderCartCount(count)' in PORTAL
    assert 'class="js-cart-icon"' not in SHELL
    assert 'class="js-cart-badge"' in SHELL
    assert 'cartBadge.textContent = String(count)' in SHELL
    assert 'cartBadge.hidden = count === 0' in SHELL
    assert 'window.setPortalHeaderCartCount(window.__portalCartCount || 0)' in SHELL
    assert 'window.__portalCartCount = (d && d.count) || 0' in PORTAL
