from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "static" / "shell.js").read_text()
PORTAL = (ROOT / "static" / "client-portal.html").read_text()


def test_portal_shell_cart_sits_before_my_path_and_is_portal_only():
    cart = SHELL.index('js-portal-cart-btn')
    my_path_append = SHELL.index('bar.appendChild(mypathBtn)')
    assert cart < my_path_append
    assert '/^\\/portal\\/' in SHELL


def test_portal_home_opens_the_hub_instead_of_the_login_page():
    assert 'typeof window.showTab === "function"' in SHELL
    assert 'window.showTab("hub")' in SHELL
    assert 'location.href = "/"' in SHELL


def test_header_cart_opens_dedicated_portal_cart_not_embedded_checkout():
    assert 'window.openPortalCart(true)' in SHELL
    assert 'window.openPortalOrderBasket(true)' not in SHELL
    assert 'function openPortalCart(captureContext)' in PORTAL
    assert 'showTab("cart")' in PORTAL
    assert 'window.openPortalCart = openPortalCart' in PORTAL
    assert 'if (name === \'cart\') loadCart()' in PORTAL


def test_review_order_still_opens_embedded_checkout():
    assert 'openPortalOrderBasket(false)' in PORTAL
    # Final review I5: #portal-order-basket left `current` for the My Remedies
    # door, so "current" alone opened Scans and left the basket hidden. Both
    # panels are named through portalPanelFor(), which picks the shell one only
    # while the shell is live. tests/test_portal_hash_routes.js proves each of
    # them resolves to a door that actually reveals the basket.
    assert 'showTab(portalPanelFor({panel:"current", shellPanel:"remedy-detail"}))' in PORTAL
    assert 'id="portal-order-basket"' in PORTAL
    assert 'document.getElementById("portal-order-basket")' in PORTAL


def test_legacy_portals_always_render_a_dedicated_cart_panel():
    assert 'actTiles.push(["cart", "My Cart"' in PORTAL
    assert '${_hub ? `<section data-panel="cart"' in PORTAL
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


def test_header_uses_button_highlights_instead_of_the_fuzzy_rewards_orb():
    css = (ROOT / "static" / "shell.css").read_text()
    assert 'js-orb' not in SHELL
    assert 'js-orb' not in css
    assert 'walletBtn.setAttribute("data-glow", "0")' in SHELL
    assert '.js-mypath-btn[data-glow="3"]' in css


def test_header_controls_show_active_and_content_states():
    css = (ROOT / "static" / "shell.css").read_text()
    assert 'function setButtonActive(button, active)' in SHELL
    assert 'cartBtn.classList.toggle("js-header-active", count > 0)' in SHELL
    assert 'setButtonActive(mypathBtn, drawer.classList.contains("open"))' in SHELL
    assert '#journey-shell .js-header-active' in css
