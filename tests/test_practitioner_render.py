import pytest
from dashboard import practitioner_render as pr


def _view(**over):
    """Minimal payload: name only. This is what EVERY practitioner renders
    today, since zero of the 23 are self-authored."""
    v = {"slug": "mary-boyd", "practitioner_name": "Mary Boyd",
         "practice_name": "", "bio": "", "photo_url": "", "logo_url": "",
         "services": [], "location": "", "accepting_clients": True,
         "featured_products": [], "catalog_url": "/begin/explore",
         "profit_disclosure": "Your practitioner earns a portion of what you"
                              " spend here. Your price is the same either way.",
         "tagline": "", "how_i_work": ""}
    v.update(over)
    return v


def test_name_only_page_is_a_complete_document():
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<h1>Mary Boyd</h1>" in html
    assert "<title>Mary Boyd</title>" in html


def test_noindex_is_present_on_every_page():
    """Section 5a never lifts noindex. 5b owns that, behind a content bar."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '<meta name="robots" content="noindex">' in html


def test_markup_in_a_practitioner_name_is_escaped():
    html = pr.render_page_html(
        _view(practitioner_name='Mary <script>alert(1)</script> Boyd'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_quotes_in_an_attribute_value_are_escaped():
    """A raw double quote in a meta content= attribute breaks out of it."""
    html = pr.render_page_html(
        _view(tagline='She said "hello" and left'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '"hello"' not in html
    assert "&quot;hello&quot;" in html
