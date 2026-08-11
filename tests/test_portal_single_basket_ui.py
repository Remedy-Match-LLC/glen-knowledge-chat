"""Every first-party product CTA in the portal feeds one shared order basket."""
import pathlib
import re


HTML = pathlib.Path("static/client-portal.html").read_text()


def _function(name):
    match = re.search(rf"function {name}\([^)]*\)\{{[\s\S]*?\n\}}\n", HTML)
    assert match, f"{name} function not found"
    return match.group(0)


def test_replenish_adds_to_shared_basket_without_starting_checkout():
    start = HTML.index("if (reorderBtn) {")
    end = HTML.index("\n    return;", start)
    block = HTML[start:end]
    assert "await addItemToBasket(slug, 1)" in block
    assert "/checkout" not in block
    assert "stripe_url" not in block


def test_catalog_chat_suggestions_add_to_basket():
    fn = _function("renderSuggestion")
    assert 'class="btn chat-sugg-btn chat-order-btn"' in fn
    assert "await addItemToBasket(orderBtn.getAttribute" in fn
    assert "Order now" not in fn
    assert "View product" in fn  # non-catalog/third-party links are not cart CTAs


def test_only_shared_basket_review_starts_product_checkout():
    for name in ("reorderItem", "orderWishlist", "addItemToBasket"):
        assert "/checkout" not in _function(name)
    checkout = _function("reorder")
    assert "/api/portal/${encodeURIComponent(token)}/checkout" in checkout
    assert "Please confirm your order" in checkout
