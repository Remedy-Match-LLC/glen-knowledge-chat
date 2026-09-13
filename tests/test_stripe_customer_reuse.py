"""One Stripe Customer per person, not one per checkout session.

Measured 2026-09-12: eight emails held 21 records more than they should, Ashley
King six of them. Stripe Checkout mints a Customer whenever a session needs one,
and `customer_email` alone never looks for an existing match.
"""
import pytest
from dashboard import stripe_pay


@pytest.fixture
def posted(monkeypatch):
    """Capture the params sent to /checkout/sessions."""
    seen = {}

    def fake_post(path, params, **kw):
        seen["path"] = path
        seen["params"] = dict(params)
        return {"id": "cs_test", "url": "https://checkout.example/x"}

    monkeypatch.setattr(stripe_pay, "_post", fake_post)
    return seen


def _find_returns(monkeypatch, cid):
    monkeypatch.setattr(stripe_pay, "_find_or_create_customer", lambda e: cid)


def test_save_card_binds_the_existing_customer(monkeypatch, posted):
    _find_returns(monkeypatch, "cus_existing")
    stripe_pay.create_checkout_session(
        1000, customer_email="repeat@example.com", description="d", metadata={},
        success_url="s", cancel_url="c", save_card=True)
    p = posted["params"]
    assert p["customer"] == "cus_existing"
    assert "customer_email" not in p, "Stripe rejects customer and customer_email together"
    assert "customer_creation" not in p, "customer_creation is what minted the duplicate"
    assert p["payment_intent_data[setup_future_usage]"] == "off_session"


def test_subscription_mode_binds_the_existing_customer(monkeypatch, posted):
    _find_returns(monkeypatch, "cus_existing")
    stripe_pay.create_price_checkout_session(
        "price_1", mode="subscription", customer_email="repeat@example.com",
        metadata={}, success_url="s", cancel_url="c")
    p = posted["params"]
    assert p["customer"] == "cus_existing"
    assert "customer_email" not in p


def test_a_lookup_that_finds_nothing_falls_back_and_never_blocks_payment(monkeypatch, posted):
    """The fallback matters more than the fix. A Stripe hiccup must not stop a
    customer paying, so an unresolved lookup keeps the old behaviour."""
    _find_returns(monkeypatch, "")
    stripe_pay.create_checkout_session(
        1000, customer_email="new@example.com", description="d", metadata={},
        success_url="s", cancel_url="c", save_card=True)
    p = posted["params"]
    assert p["customer_email"] == "new@example.com"
    assert p["customer_creation"] == "always"
    assert "customer" not in p


def test_a_plain_checkout_is_left_byte_for_byte_alone(monkeypatch, posted):
    """No save_card, no subscription: Stripe's customer_creation default is
    if_required and these were never the source of duplicates. Touching them would
    be risk without evidence."""
    def _boom(e):
        raise AssertionError("a plain payment session must not do a customer lookup")
    monkeypatch.setattr(stripe_pay, "_find_or_create_customer", _boom)
    stripe_pay.create_checkout_session(
        1000, customer_email="plain@example.com", description="d", metadata={},
        success_url="s", cancel_url="c")
    p = posted["params"]
    assert p["customer_email"] == "plain@example.com"
    assert "customer" not in p and "customer_creation" not in p


def test_attach_never_sets_both_keys(monkeypatch):
    """The invariant, stated once. Stripe 400s if both are present."""
    _find_returns(monkeypatch, "cus_x")
    a = stripe_pay._attach_customer({"customer_email": "x@example.com"}, "x@example.com")
    assert ("customer" in a) != ("customer_email" in a)
    _find_returns(monkeypatch, "")
    b = stripe_pay._attach_customer({}, "y@example.com")
    assert ("customer" in b) != ("customer_email" in b)


def test_no_email_leaves_the_session_anonymous(monkeypatch, posted):
    def _boom(e):
        raise AssertionError("must not look up a customer with no email")
    monkeypatch.setattr(stripe_pay, "_find_or_create_customer", _boom)
    out = stripe_pay._attach_customer({}, "")
    assert out == {}
