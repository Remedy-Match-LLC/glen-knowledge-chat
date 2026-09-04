"""Tell Glen and Rae when a store order arrives, because GrooveKart no longer can.

GrooveTech's mail service has refused delivery to support@remedymatch.com since
around 28 August ("451 4.7.1 Intentional policy rejection"), and its webmail
certificate expired on 30 August. That mailbox is how Rae learned an order had
come in; she then opened the Groove back office for the products and address.

We already receive the order webhook, so we can send that notification ourselves.
Interim, until the domain moves to Google Workspace.

The email has to carry what Rae actually needs to pack a box: what was ordered
and how many, who it goes to, and where. A notification that makes her go and
look something up has not replaced anything.
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
    "products": [{"product_name": "Clear Lens Eye Drops", "product_quantity": "1",
                  "product_price": "69.970000", "product_reference": "CLED-5ML"}],
    # The REAL key names GrooveKart sends, with synthetic values. Written from an
    # invented shape (address1/phone) the first time, which passed while producing
    # an email with no street address on it.
    "delivery": {"firstname": "Stephanie", "lastname": "Greenwood",
                 "address": "12 Elm St", "alias": "My address", "city": "Boise",
                 "state_name": "Idaho", "postcode": "83702",
                 "country": "United States", "phone_mobile": "2085550101"},
}


def test_the_subject_names_the_order_and_the_customer():
    subject, _ = gkn.order_email(PAYLOAD)
    assert "1028" in subject
    assert "Stephanie Greenwood" in subject
    assert "82.97" in subject


def test_the_body_lists_what_was_ordered_with_quantities():
    _, body = gkn.order_email(PAYLOAD)
    assert "1 x Clear Lens Eye Drops" in body


def test_the_body_carries_the_full_shipping_address():
    """Without this Rae still has to open the back office, which is the thing
    that is currently failing."""
    _, body = gkn.order_email(PAYLOAD)
    for part in ("12 Elm St", "Boise", "Idaho", "83702", "2085550101"):
        assert part in body, part


def test_the_body_shows_the_money_broken_out():
    _, body = gkn.order_email(PAYLOAD)
    for part in ("$69.97", "$13.00", "$82.97", "Credit Card", "USPS"):
        assert part in body, part


def test_a_discount_is_shown_only_when_there_is_one():
    _, body = gkn.order_email(PAYLOAD)
    assert "Discount" not in body
    p = dict(PAYLOAD, total_discounts="10.000000")
    assert "-$10.00" in gkn.order_email(p)[1]


def test_several_products_are_all_listed():
    p = dict(PAYLOAD, products=[
        {"product_name": "Lipid Cleanse", "product_quantity": "2", "product_price": "69.970000"},
        {"product_name": "Brain Cleanse", "product_quantity": "1", "product_price": "69.970000"}])
    _, body = gkn.order_email(p)
    assert "2 x Lipid Cleanse" in body and "1 x Brain Cleanse" in body


def test_a_sparse_payload_still_produces_a_sendable_email():
    """A notification that raises is worse than a plain one: the webhook must
    never fail because a field was missing."""
    subject, body = gkn.order_email({"id": 9})
    assert "9" in subject and body.strip()


def test_junk_never_raises():
    for bad in ({}, {"products": "not-a-list"}, {"delivery": "nope"}, {"products": [None, 7]}):
        subject, body = gkn.order_email(bad)
        assert isinstance(subject, str) and isinstance(body, str)


def test_the_email_says_why_it_exists():
    """Rae should know this is standing in for the Groove notification, not a
    second copy of it."""
    _, body = gkn.order_email(PAYLOAD)
    assert "support@remedymatch.com" in body
    assert "not arriving" in body, "the body no longer explains why we are sending it"
    assert "GrooveKart" in body


def test_the_webhook_sends_the_notification_and_cannot_fail_because_of_it():
    """Parsed, not grepped. Two properties matter: the webhook calls it, and a
    mail fault cannot reach GrooveKart as a 500 (they would retry-storm)."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)
    hook = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                and f.name == "groovekart_webhook")
    assert any(isinstance(n, ast.Name) and n.id == "_notify_store_order"
               for n in ast.walk(hook)), "the webhook does not send a notification"
    helper = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                  and f.name == "_notify_store_order")
    assert any(isinstance(n, ast.Try) for n in ast.walk(helper)), "notifier is unguarded"
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(helper)), "notifier can raise"
    # One send per recipient. _is_undeliverable() treats any To containing a
    # space as undeliverable, so a comma-joined list sends to NOBODY and raises
    # nothing. Assert the loop walks RECIPIENTS itself, not something built from it.
    loops = [n for n in ast.walk(helper) if isinstance(n, ast.For)]
    assert loops, "recipients are not sent to individually"
    assert any(isinstance(n.iter, ast.Attribute) and n.iter.attr == "RECIPIENTS"
               for n in loops), "the send loop does not iterate RECIPIENTS directly"
    # (No substring check on the source: the first version of this matched the
    #  word "join" in the helper's own docstring, which is the failure mode these
    #  AST assertions exist to avoid.)


def test_the_street_address_survives_the_real_key_names():
    """GrooveKart sends `address`/`phone_mobile`, not `address1`/`phone`. This is
    the assertion that would have caught the empty street."""
    _, body = gkn.order_email(PAYLOAD)
    assert "12 Elm St" in body and "2085550101" in body


def test_prestashop_style_keys_are_still_accepted():
    p = dict(PAYLOAD, delivery={"firstname": "A", "lastname": "B",
                                "address1": "9 Oak Rd", "city": "Hilo",
                                "state_name": "Hawaii", "postcode": "96720",
                                "phone": "8085550000"})
    _, body = gkn.order_email(p)
    assert "9 Oak Rd" in body and "8085550000" in body


def test_the_sku_is_shown_so_it_can_be_picked_without_looking_it_up():
    _, body = gkn.order_email(PAYLOAD)
    assert "[CLED-5ML]" in body
