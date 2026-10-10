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
                        "ingredients": ["Magnesium"], "qty_pricing": True,
                        "bottle_type": "30 Caps"},   # as the real product; refill needs capsules
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
    # Both catalog-enabled checkout surfaces (Orders panel and the Biofield
    # "Order your remedies" basket) must authorize active catalog-search adds.
    assert html.count("catalog_order:true") >= 2
    assert "checkout_request_id:place.dataset.checkoutRequestId" in html
    assert "for(let attempt=0; attempt<2; attempt++)" in html
    assert "/product-search?q=" in html
    assert "Cellophane refill pack" in html
    assert 'data-orders-split=' in html
    assert 'data-cart-split=' in html
    assert 'set-format-quantities' in html
    assert 'ordersBasket.flatMap' in html


def test_orders_basket_offers_zelle_and_shows_its_instructions():
    # Carol Hemphill, 2026-10-10: the Orders basket went straight to Stripe with no
    # Zelle choice. It now sends the chosen method and renders the Zelle reply.
    html = (Path(appmod.__file__).parent / "static" / "client-portal.html").read_text()

    assert 'id="ordersPaymentMethods"' in html
    assert 'name="ordersPayMethod" value="zelle"' in html
    assert "method:ordersPayMethod()," in html
    assert 'if(j.method === "zelle" && j.pay_instructions)' in html
    assert 'sessionStorage.setItem("rm_orders_zelle", zelleInstructionsHtml(j))' in html
    # The placed order must leave the basket, or a re-render re-arms "Place order"
    # and a second tap creates a second unpaid order.
    assert "ordersBasket = []; saveOrdersBasket(); renderOrdersBasket();" in html
