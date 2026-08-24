"""Tests for /api/practitioner/dropship/quote and /api/practitioner/dropship/checkout.

Monkeypatches: _practitioner_session_pid, _pp.portal_data, appmod._dropship.build_dropship_order,
_ingest_order, _STRIPE_ACTIVE.
"""

from pathlib import Path

import app as appmod


def test_dropship_order_card_has_product_lookup():
    page = (Path(__file__).parents[1] / "static" / "practitioner-dropship.html").read_text()
    assert 'id="product-search"' in page
    assert "/api/practitioner/catalog" in page
    assert "addProduct(product.slug)" in page


def test_dropship_auth_wall_requests_link_that_returns_to_order():
    page = (Path(__file__).parents[1] / "static" / "practitioner-dropship.html").read_text()
    assert 'id="signin-email"' in page
    assert 'id="signin-send"' in page
    assert "return_to:'/practitioner/dropship'" in page
    assert "brings you directly back to this order page" in page


def test_practitioner_login_request_carries_safe_dropship_return(monkeypatch):
    monkeypatch.setattr(appmod._pp, "find_practitioner_id_by_email", lambda email: "p1")
    monkeypatch.setattr(appmod._pp, "create_magic_link_token", lambda pid, email: "MAGIC")
    sent = []
    monkeypatch.setattr(appmod, "_send_practitioner_magic_link",
                        lambda email, name, url: sent.append(url))

    response = appmod.app.test_client().post(
        "/practitioner/login-request",
        json={"email": "doc@example.com", "return_to": "/practitioner/dropship"})

    assert response.status_code == 200
    assert "return_to=%2Fpractitioner%2Fdropship" in sent[0]


def test_practitioner_login_verify_returns_to_dropship(monkeypatch):
    monkeypatch.setattr(appmod._pp, "consume_magic_link", lambda token: "p1")
    monkeypatch.setattr(appmod._pp, "create_session_token", lambda pid: "SESSION")

    response = appmod.app.test_client().post(
        "/practitioner/login-verify",
        data={"token": "MAGIC", "return_to": "/practitioner/dropship"},
        follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/practitioner/dropship?token=SESSION")


def test_practitioner_login_verify_rejects_external_return(monkeypatch):
    monkeypatch.setattr(appmod._pp, "consume_magic_link", lambda token: "p1")
    monkeypatch.setattr(appmod._pp, "create_session_token", lambda pid: "SESSION")

    response = appmod.app.test_client().post(
        "/practitioner/login-verify",
        data={"token": "MAGIC", "return_to": "https://evil.example/steal"},
        follow_redirects=False)

    assert response.headers["Location"].endswith(
        "/practitioner/portal?token=SESSION")


def _auth(monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: "p1")
    monkeypatch.setattr(appmod._pp, "portal_data",
        lambda pid: {"modules_completed": 0, "email": "doc@x.com", "name": "Doc",
                     "wholesale_unlocked": True,
                     "cart": [{"slug": "brain-boost", "qty": 6}]})


def test_dropship_quote_requires_auth(monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    assert appmod.app.test_client().post(
        "/api/practitioner/dropship/quote", json={}).status_code == 401


def test_practitioner_catalog_search_requires_auth(monkeypatch):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    assert appmod.app.test_client().get(
        "/api/practitioner/catalog?q=brain").status_code == 401


def test_practitioner_catalog_search_returns_only_orderable_matches(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod._pp.pricing, "_load_catalog", lambda: {
        "brain-boost": {"name": "Brain Boost", "price_cents": 6997},
        "brain-external": {"name": "Brain External", "price_cents": 0,
                           "info_only": True},
        "liver-support": {"name": "Liver Support", "price_cents": 5997},
    })

    r = appmod.app.test_client().get("/api/practitioner/catalog?q=brain")

    assert r.status_code == 200
    assert r.get_json() == {
        "ok": True,
        "products": [{"slug": "brain-boost", "name": "Brain Boost"}],
    }


def test_dropship_quote_uses_canonical_practitioner_quote(monkeypatch):
    _auth(monkeypatch)
    captured = {}
    def fake_quote(items, practitioner):
        captured.update({"items": items, "practitioner": practitioner})
        return {"lines": [{"slug": "brain-boost", "qty": 6,
                           "unit_cents": 4000, "line_cents": 24000}],
                "subtotal_cents": 24000}
    monkeypatch.setattr(appmod._dropship, "quote_dropship_cart", fake_quote)

    r = appmod.app.test_client().get("/api/practitioner/dropship/quote")

    assert r.status_code == 200
    assert r.get_json()["subtotal_cents"] == 24000
    assert captured["practitioner"] == {"id": "p1", "modules_completed": 0}


def test_dropship_checkout_ships_to_patient(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod, "_price_cart",
                        lambda *a, **k: {"shipping_cents": 1300})
    monkeypatch.setattr(appmod._dropship, "build_dropship_order",
        lambda *a, **k: {"ok": True, "invoice_id": "INV", "total": 352.60,
                         "customer_id": "C1", "source": "dropship",
                         "ship_to": k.get("patient_ship"), "method": "zelle",
                         "get_cents": 0, "shipping_cents": k.get("shipping_cents")})
    cap = {}
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: cap.update(kw))
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", False)
    # stub cart_clear so it doesn't hit a real DB
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: None)
    r = appmod.app.test_client().post(
        "/api/practitioner/dropship/checkout",
        json={"method": "zelle",
              "patient_address": {"name": "Pat", "state": "CA", "country": "US",
                                  "street": "1 Main St", "city": "Los Angeles", "zip": "90001"}})
    assert r.status_code == 200
    assert cap["source"] == "dropship"
    assert cap["address"]["name"] == "Pat"
    assert cap["shipping_cents"] == 1300


def test_card_checkout_keeps_cart_until_payment(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod, "_price_cart", lambda *a, **k: {"shipping_cents": 1300})
    monkeypatch.setattr(appmod._dropship, "build_dropship_order", lambda *a, **k: {
        "ok": True, "invoice_id": "INV", "total": 69.0, "source": "dropship",
        "qbo_payload": {}, "get_cents": 0, "shipping_cents": 1300,
    })
    cleared = []
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: cleared.append(pid))
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", True)
    monkeypatch.setattr(appmod, "_stripe_checkout_url_for_order",
                        lambda *a, **k: "https://checkout.stripe.test/session")
    r = appmod.app.test_client().post("/api/practitioner/dropship/checkout", json={
        "method": "card", "token": "tok",
        "patient_address": {"name": "Pat", "street": "1 Main", "city": "Austin",
                            "state": "TX", "zip": "78701", "country": "US"},
    })
    assert r.status_code == 200
    assert r.get_json()["stripe_url"].startswith("https://checkout.stripe.test/")
    assert cleared == []


def test_dropship_stripe_cancel_returns_to_draft(monkeypatch):
    from dashboard import stripe_pay
    captured = {}
    monkeypatch.setattr(stripe_pay, "create_checkout_session",
                        lambda *a, **kw: captured.update(kw) or {"url": "https://stripe.test"})
    out = {"total": 93, "invoice_id": "ref", "source": "dropship",
           "practitioner_id": "p1"}
    assert appmod._stripe_checkout_url_for_order(out, "doc@example.com", "tok")
    assert "/practitioner/dropship?payment=cancelled&token=tok" in captured["cancel_url"]
    assert captured["metadata"]["practitioner_id"] == "p1"


def test_console_can_inspect_practitioner_cart(monkeypatch):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod._pp, "cart_items",
                        lambda pid: [{"slug": "iron-out", "qty": 2}])
    r = appmod.app.test_client().get("/api/console/practitioners/p1/cart")
    assert r.status_code == 200
    assert r.get_json()["item_count"] == 2


def test_dropship_checkout_requires_patient_address(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod._dropship, "build_dropship_order",
        lambda *a, **k: {"ok": True, "invoice_id": "INV", "total": 100.0,
                         "customer_id": "C1", "source": "dropship",
                         "ship_to": k.get("patient_ship"), "method": "zelle",
                         "get_cents": 0})
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", False)
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: None)
    # POST without patient_address → 400
    r = appmod.app.test_client().post(
        "/api/practitioner/dropship/checkout",
        json={"method": "zelle"})
    assert r.status_code == 400
    data = r.get_json()
    assert data.get("ok") is False
    assert "patient_address" in (data.get("error") or "")


def test_dropship_checkout_blocks_practitioner_as_recipient_without_confirmation(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: None)
    r = appmod.app.test_client().post(
        "/api/practitioner/dropship/checkout",
        json={"method": "zelle",
              "patient_address": {"name": "  DOC ", "state": "CA", "country": "US",
                                  "street": "1 Main St", "city": "Los Angeles",
                                  "zip": "90001"}})
    assert r.status_code == 400
    assert r.get_json()["code"] == "recipient_name_matches_practitioner"


def test_dropship_checkout_allows_confirmed_practitioner_recipient(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(appmod, "_price_cart",
                        lambda *a, **k: {"shipping_cents": 1300})
    monkeypatch.setattr(appmod._dropship, "build_dropship_order",
        lambda *a, **k: {"ok": True, "invoice_id": "INV", "total": 100.0,
                         "customer_id": "C1", "source": "dropship",
                         "ship_to": k.get("patient_ship"), "method": "zelle",
                         "get_cents": 0, "shipping_cents": k.get("shipping_cents")})
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", False)
    monkeypatch.setattr(appmod._pp, "cart_clear", lambda pid: None)
    r = appmod.app.test_client().post(
        "/api/practitioner/dropship/checkout",
        json={"method": "zelle", "recipient_name_confirmed": True,
              "patient_address": {"name": "Doc", "state": "CA", "country": "US",
                                  "street": "1 Main St", "city": "Los Angeles",
                                  "zip": "90001"}})
    assert r.status_code == 200
