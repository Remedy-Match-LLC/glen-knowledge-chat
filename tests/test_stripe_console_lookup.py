from pathlib import Path


def test_lookup_sanitizes_stripe_records(monkeypatch):
    from dashboard import stripe_lookup

    def fake_get(path, **kwargs):
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


def test_customer_search_pins_an_api_version_that_supports_search(monkeypatch):
    """Stripe Search needs API version 2020-08-27+. This account defaults to
    2016-07-06, so an unversioned search 400s and the Console shows
    "Stripe lookup is temporarily unavailable" for every query. Drop the pin and
    this test fails, instead of the failure surfacing as a 502 in front of Rae."""
    from dashboard import stripe_lookup, stripe_pay

    seen = []

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"data": []}

    def fake_requests_get(url, **kwargs):
        seen.append((url, kwargs.get("headers") or {}))
        return _Resp()

    monkeypatch.setattr(stripe_pay, "_key", lambda: "sk_test_stub")
    monkeypatch.setattr(stripe_pay.requests, "get", fake_requests_get)

    stripe_lookup.lookup("Metzen")

    searches = [(url, headers) for url, headers in seen if "/customers/search" in url]
    assert searches, "lookup never called /customers/search"
    for url, headers in searches:
        version = headers.get("Stripe-Version")
        assert version, f"search sent no Stripe-Version header: {url}"
        assert version >= "2020-08-27", f"Stripe-Version {version} is too old for search"


def test_non_search_stripe_calls_send_no_version_header(monkeypatch):
    """The version pin is deliberately per-request. Sending it on every call would
    change the response shape of reads that have been parsed against 2016-07-06."""
    from dashboard import stripe_pay

    seen = {}

    class _Resp:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {}

    def fake_requests_get(url, **kwargs):
        # .get() would hide the difference between "absent" and "None"; the whole
        # point of this test is that the kwarg is not passed at all.
        seen["headers"] = kwargs["headers"] if "headers" in kwargs else None
        seen["passed_headers_kwarg"] = "headers" in kwargs
        return _Resp()

    monkeypatch.setattr(stripe_pay, "_key", lambda: "sk_test_stub")
    monkeypatch.setattr(stripe_pay.requests, "get", fake_requests_get)

    stripe_pay._get("/charges?limit=1")
    assert seen["passed_headers_kwarg"] is False, "unversioned calls must not pass a headers kwarg"
