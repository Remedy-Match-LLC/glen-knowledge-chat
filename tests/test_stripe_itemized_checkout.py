from dashboard import stripe_pay


def test_itemized_checkout_builds_one_visible_line_per_remedy(monkeypatch):
    captured = {}

    def fake_post(path, params):
        captured.update(params)
        return {"id": "cs_1", "url": "https://stripe.test/cs_1"}

    monkeypatch.setattr(stripe_pay, "_post", fake_post)
    result = stripe_pay.create_itemized_checkout_session(
        [
            {"name": "Liver Support", "qty": 1, "unit_cents": 6997},
            {"name": "Heart Health", "qty": 2, "unit_cents": 5997},
        ],
        customer_email="carol@example.com", metadata={"kind": "reorder"},
        success_url="https://example.test/success",
        cancel_url="https://example.test/cancel", collect_shipping=True,
    )

    assert result["url"] == "https://stripe.test/cs_1"
    assert captured["line_items[0][price_data][product_data][name]"] == "Liver Support"
    assert captured["line_items[0][price_data][unit_amount]"] == "6997"
    assert captured["line_items[1][price_data][product_data][name]"] == "Heart Health"
    assert captured["line_items[1][quantity]"] == "2"
