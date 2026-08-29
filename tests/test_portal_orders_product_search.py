from pathlib import Path

import app as appmod


class _Cx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_product_search_requires_valid_portal_and_returns_sellable_matches(monkeypatch):
    products = {
        "brain-boost": {"name": "Brain Boost", "description": "Focus support",
                        "ingredients": ["Magnesium"], "qty_pricing": True},
        "calm": {"name": "Calm Formula", "description": "Relaxation",
                 "ingredients": ["Magnesium"], "inactive": True},
        "guide": {"name": "Wellness Guide", "description": "Information",
                  "info_only": True},
    }
    monkeypatch.setattr(appmod, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": products})
    monkeypatch.setattr(appmod.db, "connect", lambda *_: _Cx())
    monkeypatch.setattr(appmod, "_portal_record_for", lambda cx, token: {"email": "steve@example.com"})
    monkeypatch.setattr(appmod, "_get_product", lambda slug: products.get(slug))

    with appmod.app.test_request_context("/api/portal/good/product-search?q=magnesium"):
        response = appmod.api_client_portal_product_search("good")
    payload = response.get_json()

    assert payload["products"] == [{
        "slug": "brain-boost", "name": "Brain Boost", "refill_eligible": True,
    }]


def test_product_search_rejects_invalid_portal(monkeypatch):
    monkeypatch.setattr(appmod, "_PORTAL_CART_ENABLED", True)
    monkeypatch.setattr(appmod.db, "connect", lambda *_: _Cx())
    monkeypatch.setattr(appmod, "_portal_record_for", lambda cx, token: None)

    with appmod.app.test_request_context("/api/portal/bad/product-search?q=brain"):
        response, status = appmod.api_client_portal_product_search("bad")

    assert status == 404
    assert response.get_json()["error"] == "not found"


def test_orders_panel_contains_search_basket_and_catalog_checkout():
    html = (Path(appmod.__file__).parent / "static" / "client-portal.html").read_text()

    assert 'id="orders-product-search-form"' in html
    assert 'data-orders-add=' in html
    assert 'id="orders-basket"' in html
    assert 'id="orders-place-order"' in html
    assert "catalog_order:true" in html
    assert "checkout_request_id:place.dataset.checkoutRequestId" in html
    assert "for(let attempt=0; attempt<2; attempt++)" in html
    assert "/product-search?q=" in html
    assert "Cellophane refill pack" in html
    assert 'data-orders-split=' in html
    assert 'data-cart-split=' in html
    assert 'set-format-quantities' in html
    assert 'ordersBasket.flatMap' in html
