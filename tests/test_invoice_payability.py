"""Whether a client can pay an invoice is not the same question as whether they
can still change it.

The invoice page gated its pay button on `editable`, which is true only for
`proposed` and `confirmed`. Every other status showed the customer their invoice
with no way to pay it. On 2026-09-03 that was 11 unpaid orders worth $2,051.94,
including two of Ashley King's drop-ships that had already SHIPPED (status `done`)
and gone a month uncollected.
"""
import pytest

from dashboard import invoice_payability as ip


@pytest.mark.parametrize("status", ["new", "proposed", "confirmed", "packed",
                                    "shipped", "delivered", "done"])
def test_an_unpaid_order_is_payable_at_every_live_status(status):
    assert ip.is_payable(status, "unpaid") is True


def test_a_shipped_but_unpaid_order_is_payable():
    """Ashley's #115 and #116: goods went out, the money never came in."""
    assert ip.is_payable("done", "unpaid") is True


def test_a_paid_order_is_not_payable_again():
    for status in ("confirmed", "done", "delivered"):
        assert ip.is_payable(status, "paid") is False


def test_a_cancelled_order_is_never_payable():
    for pay in ("unpaid", "", None):
        assert ip.is_payable("cancelled", pay) is False


def test_a_refunded_order_is_not_payable():
    assert ip.is_payable("done", "refunded") is False


def test_missing_or_junk_input_does_not_open_the_button():
    # It decides whether a customer is asked for money; unknown state means no ask.
    assert ip.is_payable(None, None) is False
    assert ip.is_payable("", "unpaid") is False
    assert ip.is_payable(7, {"a": 1}) is False


def test_case_and_padding_do_not_change_the_answer():
    assert ip.is_payable("  Done ", " UNPAID ") is True
    assert ip.is_payable("CANCELLED", "unpaid") is False


def test_every_non_cancelled_status_the_app_defines_is_payable_when_unpaid():
    """Guard the whole vocabulary, not the seven named above: a status added later
    must not silently land on the unpayable side."""
    from dashboard.orders import ORDER_STATUSES
    unpayable = [s for s in ORDER_STATUSES
                 if s not in ("cancelled", "paid") and not ip.is_payable(s, "unpaid")]
    assert unpayable == [], f"unpaid orders that cannot be paid: {unpayable}"
