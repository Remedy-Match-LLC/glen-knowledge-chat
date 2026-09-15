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


def test_store_cart_exposes_a_refresh_hook():
    # Fix wave item 3 (MINOR): the badge was read once, at shell-mount time,
    # and never again -- Add to cart on the product page could not update it.
    # A named, guarded (typeof) hook lets begin-product.html re-pull the
    # count after a successful add.
    assert "window.refreshStoreCartCount" in SHELL


def test_store_cart_button_requires_the_dark_launch_flag_too():
    # Fix wave item 6: /begin/cart is dark-launched separately from the rest
    # of the cart routes (cart_page in the /api/cart JSON). The button links
    # to that page, so it must not show just because /api/cart answered ok.
    assert "d.ok || !d.cart_page" in SHELL
