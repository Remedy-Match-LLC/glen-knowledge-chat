from pathlib import Path


def test_lookup_sanitizes_stripe_records(monkeypatch):
    from dashboard import stripe_lookup

    def fake_get(path):
        if path.startswith("/customers/search"):
            return {"data": [{"id": "cus_1", "name": "Anne S Metzen",
                              "email": "anne@example.com", "delinquent": False,
                              "default_source": "card_secret"}]}
        if path.startswith("/charges"):
            return {"data": [{"id": "ch_1", "amount": 99000, "currency": "usd",
                              "status": "succeeded", "paid": True,
                              "payment_method": "pm_secret", "metadata": {"kind": "prepay"}}]}
        if path.startswith("/invoices"):
            return {"data": []}
        if path.startswith("/subscriptions"):
            return {"data": []}
        raise AssertionError(path)

    monkeypatch.setattr(stripe_lookup.stripe_pay, "_get", fake_get)
    result = stripe_lookup.lookup("Metzen")
    assert result["matches"][0]["payments"][0]["amount"] == 99000
    assert "payment_method" not in result["matches"][0]["payments"][0]
    assert "default_source" not in result["matches"][0]["customer"]


def test_lookup_rejects_too_short_query():
    from dashboard import stripe_lookup
    try:
        stripe_lookup.lookup("A")
    except ValueError as exc:
        assert "at least 2" in str(exc)
    else:
        raise AssertionError("short query was accepted")


def test_console_money_exposes_stripe_lookup_ui():
    html = (Path(__file__).resolve().parent.parent / "static" / "console-money.html").read_text()
    assert "Stripe Lookup" in html
    assert "/api/money/stripe-lookup?q=" in html
    assert "no card or bank details" in html
    payments_tab = html.index('data-tab="payments"')
    lookup_tab = html.index('data-tab="lookup"')
    receivables_tab = html.index('data-tab="receivables"')
    assert payments_tab < lookup_tab < receivables_tab


def test_lookup_route_uses_protected_helper(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    import app as appmod
    from dashboard import stripe_lookup

    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(appmod.dashboard, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(stripe_lookup, "lookup", lambda q: {"query": q, "matches": []})
    client = appmod.app.test_client()

    denied = client.get("/api/money/stripe-lookup?q=Metzen")
    assert denied.status_code == 401

    response = client.get("/api/money/stripe-lookup?q=Metzen",
                          headers={"X-Console-Key": "test-secret"})
    assert response.status_code == 200
    assert response.get_json()["data"] == {"query": "Metzen", "matches": []}
