"""Confirm the order to the person who placed it.

GrooveKart's checkout ends on a bare "Your shopping cart is empty" page. No
order number, no receipt, nothing to screenshot. Walked on 2026-09-08: Glen paid
and could not tell whether it had worked, and the capture was sitting in
Authorize.net the whole time.

We already build a notification from this payload and send it to Rae and Glen.
This sends the same facts to the buyer.

The rule this file guards hardest: it must never claim the payment succeeded.
The GrooveKart webhook signals order CREATION and carries no settlement field,
so when this fires nobody knows whether the card cleared. A receipt that asserts
payment would be a guess in the customer's inbox.
"""
import pytest

from dashboard import groovekart_notify as gkn

PAYLOAD = {
    "id": 1028, "reference": "OSYLCOYOS", "payment": "Credit Card",
    "customer_firstname": "Stephanie", "customer_lastname": "Greenwood",
    "customer_email": "slmg14@gmail.com",
    "date_add": "2026-09-03 11:47:44",
    "total_products": "69.970000", "total_shipping": "13.000000",
    "total_discounts": "0.000000", "total_paid": "82.970000",
    "carrier_name": "USPS", "products_count": 1,
    "products": [{"product_name": "Clear Lens Eye Drops", "product_quantity": "2",
                  "product_price": "69.970000", "product_reference": "CLED-5ML"}],
    "delivery": {"firstname": "Stephanie", "lastname": "Greenwood",
                 "address": "12 Elm St", "city": "Boise",
                 "state_name": "Idaho", "postcode": "83702",
                 "country": "United States", "phone_mobile": "2085550101"},
}


# ── It must not claim the payment worked ─────────────────────────────────────

def test_it_never_asserts_that_payment_succeeded():
    _, body = gkn.buyer_email(PAYLOAD)
    low = body.lower()
    for claim in ("payment received", "payment confirmed", "payment successful",
                  "thank you for your payment", "your payment has been",
                  "paid in full", "receipt for your payment"):
        assert claim not in low, f"the buyer email claims {claim!r}"


def test_it_tells_them_what_to_do_if_they_are_unsure():
    """The whole reason they are reading this is that the checkout told them
    nothing. Ordering again is the expensive mistake to prevent."""
    _, body = gkn.buyer_email(PAYLOAD)
    assert "reply to this email" in body.lower()
    assert "twice" in body.lower()


# ── It must carry the order ──────────────────────────────────────────────────

def test_the_subject_carries_the_reference_they_can_quote():
    subject, _ = gkn.buyer_email(PAYLOAD)
    assert "OSYLCOYOS" in subject


def test_the_body_lists_what_they_bought_with_quantities():
    _, body = gkn.buyer_email(PAYLOAD)
    assert "2 x Clear Lens Eye Drops" in body


def test_the_packing_sku_is_not_shown_to_the_customer():
    """CLED-5ML is there so Rae picks the right jar. To a customer it reads as
    a warehouse slip, and it is in the internal email either way."""
    _, buyer = gkn.buyer_email(PAYLOAD)
    _, internal = gkn.order_email(PAYLOAD)
    assert "CLED-5ML" not in buyer
    assert "CLED-5ML" in internal


def test_the_body_carries_the_total():
    _, body = gkn.buyer_email(PAYLOAD)
    assert "82.97" in body


def test_it_opens_with_their_first_name():
    _, body = gkn.buyer_email(PAYLOAD)
    assert body.startswith("Stephanie,")


def test_it_names_where_to_ask_for_help():
    _, body = gkn.buyer_email(PAYLOAD)
    assert "Support@RemedyMatch.com" in body


# ── It must survive whatever the webhook sends ───────────────────────────────

def test_a_payload_with_no_email_yields_no_address_to_send_to():
    assert gkn.buyer_address({"id": 1}) == ""
    assert gkn.buyer_address(None) == ""


def test_the_address_comes_back_when_there_is_one():
    assert gkn.buyer_address(PAYLOAD) == "slmg14@gmail.com"


@pytest.mark.parametrize("payload", [None, {}, {"products": "not a list"},
                                     {"delivery": "not a dict"},
                                     {"customer_firstname": None, "total_paid": None}])
def test_it_never_raises_on_a_malformed_payload(payload):
    """This runs inside the webhook. An exception returns 500 and GrooveKart
    retry-storms, which is why every function in that module is total."""
    subject, body = gkn.buyer_email(payload)
    assert isinstance(subject, str) and isinstance(body, str)
    assert subject and body


def test_an_order_with_no_delivery_block_omits_the_address_section():
    p = dict(PAYLOAD)
    p.pop("delivery")
    _, body = gkn.buyer_email(p)
    assert "Going to:" not in body
    assert "2 x Clear Lens Eye Drops" in body


# ── The webhook must actually send it, and must not risk Rae's copy ──────────

def test_the_webhook_sends_the_buyer_copy_after_the_internal_one():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    fn = src[src.index("def _notify_store_order"):]
    fn = fn[:fn.index("\ndef ")]
    assert "buyer_address(payload)" in fn
    assert "buyer_email(payload)" in fn
    # Rae's notification is sent first and the buyer send is guarded on its own,
    # so a failure reaching the customer cannot cost her the packing email.
    assert fn.index("_gkn.RECIPIENTS") < fn.index("buyer_address")
    tail = fn[fn.index("buyer_address"):]
    assert "except Exception" in tail
