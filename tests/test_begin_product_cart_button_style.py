"""The product page's Add to cart button must be styled.

renderCartControl() builds `button.btn.btn-secondary` inside `#buy-actions`. The page once
defined no rule for it, so on a phone it rendered as a bare grey system button under the gold
Order button.
"""
import re
from pathlib import Path

PAGE = (Path(__file__).parents[1] / "static" / "begin-product.html").read_text()


def _css():
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", PAGE, flags=re.S))


def test_cart_control_still_builds_the_classes_the_css_targets():
    assert "btn.className = 'btn btn-secondary';" in PAGE
    assert "wrap.id = 'buy-actions';" in PAGE


def test_add_to_cart_has_a_base_style_in_the_page_css():
    css = _css()
    rule = re.search(r"#buy-actions \.btn-secondary\{([^}]*)\}", css)
    assert rule, "no base rule for the Add to cart button"
    body = rule.group(1)
    assert "border-radius" in body and "var(--gold)" in body


def test_add_to_cart_has_hover_focus_and_phone_rules():
    css = _css()
    assert "#buy-actions .btn-secondary:hover" in css
    assert "#buy-actions .btn-secondary:focus-visible" in css
    phone = re.search(r"@media\(max-width:560px\)\{(.*?)\n    \}", css, flags=re.S)
    assert phone and "#buy-actions .btn-secondary" in phone.group(1)
