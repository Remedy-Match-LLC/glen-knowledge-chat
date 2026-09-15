from pathlib import Path

SHELL = (Path(__file__).parents[1] / "static" / "shell.js").read_text()


def test_store_pages_get_a_cart_button_that_opens_the_cart_page():
    assert "js-store-cart-btn" in SHELL
    assert '"/begin/cart"' in SHELL
    assert "/api/cart" in SHELL


def test_portal_keeps_its_own_basket_button():
    assert "js-portal-cart-btn" in SHELL
    assert "openPortalCart" in SHELL


def test_store_cart_button_is_excluded_from_the_cart_page_itself():
    # Controller ruling: the store cart button must not render on /begin/cart
    # itself, the cart page it would link to. The path test that gates the
    # button must carry an explicit exclusion for exactly /begin/cart (and
    # /begin/cart/), not just rely on the button being harmless there.
    assert "/begin/cart" in SHELL
    assert "!/^\\/begin\\/cart\\/?$/.test(location.pathname)" in SHELL


def test_store_cart_badge_hides_at_zero_while_the_button_stays_visible():
    # Review finding, fix round 1: an empty cart must not show a blank gold
    # pill. Match the portal button's pattern (cartBadge.hidden = count === 0)
    # on the badge only, so the Cart link itself still shows once /api/cart
    # is ok.
    assert "badge.hidden = n === 0" in SHELL
