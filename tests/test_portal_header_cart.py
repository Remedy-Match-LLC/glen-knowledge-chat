from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "static" / "shell.js").read_text()
PORTAL = (ROOT / "static" / "client-portal.html").read_text()


def test_portal_shell_cart_sits_before_my_path_and_is_portal_only():
    cart = SHELL.index('js-portal-cart-btn')
    my_path_append = SHELL.index('bar.appendChild(mypathBtn)')
    assert cart < my_path_append
    assert '/^\\/portal\\/' in SHELL


def test_header_cart_opens_remedy_order_basket_not_storefront_cart():
    assert 'window.openPortalOrderBasket' in SHELL
    assert 'showTab("current")' in PORTAL
    assert 'id="portal-order-basket"' in PORTAL
    assert 'document.getElementById("portal-order-basket")' in PORTAL


def test_header_cart_count_tracks_shared_remedy_basket():
    assert '#curatedOrderItems .curated-order-item' in PORTAL
    assert 'row.getAttribute("data-qty")' in PORTAL
    assert 'window.setPortalHeaderCartCount(count)' in PORTAL
    assert '"Cart (" + count + ")"' in SHELL
