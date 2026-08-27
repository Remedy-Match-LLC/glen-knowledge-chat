"""Paid-only QBO booking: create one summarized Sales Receipt for an order when
it is confirmed paid. Stripe/client detail stays itemized; QBO receives net
Physical Goods and Digital / Services totals. Idempotent and best-effort."""
import json

from . import qbo_billing
from . import orders
from . import qbo_summary


def book_sale_on_payment(cx, order):
    """Book a summarized QBO SalesReceipt from stored itemized checkout lines.
    Returns the receipt Id (existing one if already booked; None on any failure,
    when there is nothing to book, or when this caller lost the atomic claim).
    Never raises.

    Claims the booking slot (qbo_sales_receipt_id NULL -> 'PENDING', a single
    conditional UPDATE) BEFORE any QBO write, so idempotency is decided by the DB
    row rather than by the (possibly stale) `order` dict the caller passed in. Only
    the caller that wins the claim books; every other caller no-ops. This is what
    prevents a double Sales Receipt when the QBO write succeeds but the local stamp
    write fails, on a checkout-return page refresh, or in a webhook+redirect /
    alt-pay+card race."""
    try:
        existing = order.get("qbo_sales_receipt_id")
        if existing:
            # A real id means already booked; 'PENDING' means a claim is in
            # flight elsewhere. Either way, never book again.
            return None if str(existing).startswith("PENDING") else existing
        raw = order.get("qbo_lines_json")
        if not raw:
            return None
        payload = json.loads(raw) if isinstance(raw, str) else raw
        detail_lines = payload.get("lines") or []
        if not detail_lines:
            return None
        lines = qbo_summary.summarize(
            detail_lines, order.get("total_cents"), source=order.get("source") or "")
        if not lines:
            return None
        oid = order.get("id")
        if oid is None or cx is None:
            return None
        # Atomic claim BEFORE the QBO write. Losing the claim means someone else
        # already booked or is currently booking -- do not proceed.
        if not orders.claim_sales_receipt_slot(cx, oid):
            return None
        cust = qbo_billing.find_or_create_customer(order.get("email") or "",
                                                   order.get("name") or "")
        receipt_kwargs = {
            # The two summary lines are already net of cart discounts and must
            # add up exactly to the Stripe/order total.
            "discount_cents": 0,
            "tax_cents": int(payload.get("tax_cents") or 0),
            "email_to": order.get("email") or None,
            "private_note": f"order:{order.get('external_ref')}",
        }
        sr = qbo_billing.create_sales_receipt(
            cust, lines,
            **receipt_kwargs)
        sr_id = sr.get("Id")
        if sr_id:
            orders.set_order_sales_receipt_id(cx, oid, sr_id)  # 'PENDING' -> real id
        return sr_id
    except Exception as e:  # best-effort — must never break the payment path
        print(f"[qbo-sale] book_sale_on_payment skipped for order "
              f"{order.get('id')!r}: {e!r}", flush=True)
        return None
