"""Legacy paid-only QBO booking compatibility.

Portal/card sales are accounted for from processor payouts downloaded through
the bank feed. Creating a second QBO SalesReceipt here duplicates that money,
so automatic booking was retired on 2026-08-29. The function remains as a
no-op because payment settlement has many historical callers and settlement
must not depend on QBO availability.
"""

def book_sale_on_payment(cx, order):
    """Do not push paid orders to QBO; the bank feed is authoritative.

    Return an existing historical marker for caller compatibility. New orders
    return None without claiming the old booking slot or making any QBO call.
    Historical and ``DELETED:<id>`` markers remain unchanged so cleanup is
    auditable and old webhook replays cannot recreate transactions. Never raises.
    """
    existing = order.get("qbo_sales_receipt_id")
    if existing and not str(existing).startswith("PENDING"):
        return existing
    return None
