from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_practitioner_order_uses_catalog_lookup_instead_of_raw_slug():
    page = (ROOT / "static" / "practitioner-portal.html").read_text()
    assert 'id="product-search"' in page
    assert "/api/practitioner/catalog" in page
    assert "addOrderProduct(product)" in page
    assert 'placeholder="Product slug' not in page


def test_patient_catalog_has_search_that_filters_without_mutating_cart():
    page = (ROOT / "static" / "practitioner-client.html").read_text()
    assert 'id="catalog-search"' in page
    assert "catalog.filter(function (p)" in page
    assert "normalizeSearch(p.name + ' ' + p.slug)" in page
    assert "renderCartSummary();" in page
    assert "No matching formulations" in page
