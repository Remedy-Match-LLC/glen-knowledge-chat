import sqlite3

import app
from dashboard import orders as O


def _seed(monkeypatch, tmp_path):
    db_path = str(tmp_path / "orders.db")
    monkeypatch.setattr(app, "LOG_DB", db_path)
    with sqlite3.connect(db_path) as cx:
        O.init_orders_table(cx)
        cx.row_factory = sqlite3.Row
        order_id = O.upsert_order(
            cx, source="portal-reorder", external_ref="portal-checkout-1",
            email="buyer@example.com", name="A Buyer",
            items=[{"name": "Remedy Bottle", "slug": "remedy", "qty": 12}],
            total_cents=75200, shipping_cents=2300, address={}, status="new")
    return order_id


def test_paid_return_redirects_to_receipt(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    import dashboard.stripe_pay as stripe_pay
    monkeypatch.setattr(stripe_pay, "get_session", lambda _sid: {
        "payment_status": "paid", "amount_total": 75200,
        "payment_intent": "", "metadata": {
            "kind": "portal-reorder", "invoice_id": "portal-checkout-1",
            "return_to": "https://illtowell.com/portal/private-token"}})
    response = app.app.test_client().get(
        "/begin/checkout-return?session_id=cs_paid", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/order-confirmation?session_id=cs_paid")


def test_receipt_shows_order_total_items_and_portal_link(monkeypatch, tmp_path):
    order_id = _seed(monkeypatch, tmp_path)
    import dashboard.stripe_pay as stripe_pay
    monkeypatch.setattr(stripe_pay, "get_session", lambda _sid: {
        "payment_status": "paid", "amount_total": 75200,
        "customer_details": {"email": "buyer@example.com"},
        "metadata": {"kind": "portal-reorder", "invoice_id": "portal-checkout-1",
                     "return_to": "https://illtowell.com/portal/private-token"}})
    response = app.app.test_client().get(
        "/order-confirmation?session_id=cs_paid")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f"#{order_id}" in body
    assert "$752.00" in body
    assert "Remedy Bottle" in body
    assert "&times; 12" in body
    assert 'href="https://illtowell.com/portal/private-token"' in body


def test_receipt_rejects_unpaid_session(monkeypatch):
    import dashboard.stripe_pay as stripe_pay
    monkeypatch.setattr(stripe_pay, "get_session", lambda _sid: {
        "payment_status": "unpaid", "metadata": {}})
    response = app.app.test_client().get(
        "/order-confirmation?session_id=cs_unpaid")
    assert response.status_code == 400
    assert "Please do not submit another payment" in response.get_data(as_text=True)


def test_portal_checkout_carries_receipt_return_context(monkeypatch):
    captured = {}
    import dashboard.stripe_pay as stripe_pay

    def create_session(items, **kwargs):
        captured.update(kwargs)
        return {"url": "https://checkout.stripe.test/session"}

    monkeypatch.setattr(stripe_pay, "create_itemized_checkout_session", create_session)
    out = {
        "invoice_id": "portal-checkout-2", "customer_id": "",
        "kind": "portal-reorder", "total": 752.00,
        "stripe_line_items": [{"name": "Remedy Bottle", "qty": 12,
                               "unit_cents": 6075}],
        "cancel_url": "https://illtowell.com/portal/private-token",
        "return_to": "https://illtowell.com/portal/private-token",
    }

    url = app._stripe_checkout_url_for_reorder(out, "buyer@example.com")

    assert url == "https://checkout.stripe.test/session"
    assert captured["metadata"]["kind"] == "portal-reorder"
    assert captured["metadata"]["return_to"] == out["return_to"]
    assert captured["success_url"].endswith(
        "/begin/checkout-return?session_id={CHECKOUT_SESSION_ID}")


def test_portal_checkout_idempotency_changes_with_checkout_lines(monkeypatch):
    keys = []
    import dashboard.stripe_pay as stripe_pay

    def create_session(items, **kwargs):
        keys.append(kwargs["idempotency_key"])
        return {"url": "https://checkout.stripe.test/session"}

    monkeypatch.setattr(stripe_pay, "create_itemized_checkout_session", create_session)
    base = {"invoice_id": "portal-same-order", "customer_id": "",
            "kind": "portal-reorder", "total": 50.00,
            "cancel_url": "https://illtowell.com/portal/t",
            "return_to": "https://illtowell.com/portal/t"}
    first = dict(base, stripe_line_items=[{"name": "Bone Builder", "qty": 1,
                                          "unit_cents": 5000}])
    changed = dict(base, stripe_line_items=[{"name": "Bone Builder", "qty": 1,
                                            "unit_cents": 6500}])

    app._stripe_checkout_url_for_reorder(first, "anne@example.com")
    app._stripe_checkout_url_for_reorder(first, "anne@example.com")
    app._stripe_checkout_url_for_reorder(changed, "anne@example.com")

    assert keys[0] == keys[1]
    assert keys[0] != keys[2]
    assert keys[0].startswith("portal-same-order:")
