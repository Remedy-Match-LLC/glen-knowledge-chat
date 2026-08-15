"""A product page may show a different name than its invoice identity.

Immune Intelligence is sold under the brand "Happy Cow Colostrum". A client following an
Immune Intelligence recommendation would otherwise land on a page titled Happy Cow
Colostrum and reasonably think they had the wrong link. `display_name` fixes the page
without touching `name`, which is the QBO/invoice identity and must not move.
"""
import json


def _products():
    return json.load(open("data/products.json"))["products"]


def test_immune_intelligence_page_shows_both_names():
    p = _products()["immune-intelligence"]
    disp = p.get("display_name") or p["name"]
    assert "Immune Intelligence" in disp
    assert "Happy Cow Colostrum" in disp, "the brand name must still be recognisable"


def test_invoice_identity_is_not_changed_by_display_name():
    """`name` is what reaches QuickBooks. Renaming it would rewrite invoice lines."""
    p = _products()["immune-intelligence"]
    assert p["name"] == "Happy Cow Colostrum"
    assert p["name"] != p.get("display_name")


def test_display_name_is_optional():
    """Every other product must render from `name` alone, unchanged."""
    prods = _products()
    withdisp = [s for s, v in prods.items() if v.get("display_name")]
    assert withdisp == ["immune-intelligence"], withdisp
    for slug, v in prods.items():
        if v.get("name"):
            assert (v.get("display_name") or v["name"]), slug


def test_the_route_the_page_reads_uses_display_name():
    """Regression: display_name was first added to /begin/product-data, which the page
    does NOT read. The page reads /begin/product-page-data, so the title never changed.
    Pin BOTH routes, since the names differ by one word."""
    src = open("app.py").read()
    for marker in ('"slug": slug, "name": p.get("display_name") or p["name"],',):
        assert src.count(marker) == 2, (
            f"expected display_name preference in BOTH product routes, found "
            f"{src.count(marker)}")
