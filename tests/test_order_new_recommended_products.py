from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "order-new.html").read_text()


def test_edit_invoice_can_add_current_biofield_recommendations():
    assert 'id="add-recommended-btn"' in HTML
    assert "function addRecommendedProducts()" in HTML
    assert "/api/console/client-recommended-products?email=" in HTML
    assert "source:'biofield'" in HTML
    assert "Save invoice changes to apply." in HTML
