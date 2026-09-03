"""Can a client pay this invoice?

Deliberately separate from `editable`, which answers a different question: can the
client still change what is ON the invoice. The invoice page conflated the two and
gated its pay button on `editable`, which is true only for `proposed` and
`confirmed`. Every other status rendered the invoice with no way to pay it.

On 2026-09-03 that hid the pay button on 11 unpaid orders worth $2,051.94,
including two of Ashley King's drop-ships that had already SHIPPED and gone a
month uncollected. An order that has shipped is the LAST one you want unpayable.

A shipped order should not be re-priced by the customer, so `editable` stays as it
is. It should absolutely still be payable.
"""

# Only these end the obligation. Everything else -- new, packed, shipped,
# delivered, done -- is an order someone still owes money on.
_NOT_OWED_STATUS = ("cancelled",)
_SETTLED_PAY = ("paid", "refunded")


def _norm(value):
    return value.strip().lower() if isinstance(value, str) else ""


def is_payable(status, pay_status):
    """True when money is still owed on this order and we may ask for it.

    Unknown or malformed state returns False: this decides whether a customer is
    shown a pay button, and guessing "yes" asks a real person for money.
    """
    status, pay_status = _norm(status), _norm(pay_status)
    if not status:
        return False
    if status in _NOT_OWED_STATUS:
        return False
    if pay_status in _SETTLED_PAY:
        return False
    return True
