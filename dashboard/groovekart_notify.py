"""Order notification for GrooveKart orders, because GrooveKart no longer sends one.

GrooveTech's mail service has refused delivery to support@remedymatch.com since
around 2026-08-28 ("451 4.7.1 Intentional policy rejection") and its webmail
certificate expired on 2026-08-30. That mailbox is how Rae learned an order had
arrived; she then opened the Groove back office for the products and the address.

We already receive the order webhook, so we send the notification ourselves.
Interim, until remedymatch.com moves to Google Workspace.

The email carries what is needed to pack a box without opening anything else:
what was ordered and how many, who it goes to, and where. A notification that
sends someone off to look things up has not replaced the one that stopped.

Every function here is total: a notification that raises would take the webhook
down with it, and GrooveKart would retry-storm a 500.
"""

import re as _re

# Interim recipients. Rae watches the payments and packs; Glen wants sight of it.
RECIPIENTS = ("suerae1111@gmail.com", "drglenswartwout@gmail.com")

# The buyer was getting nothing at all. GrooveKart's checkout ends on a bare
# "Your shopping cart is empty" page: no order number, no receipt, nothing to
# screenshot. Confirmed by walking the store on 2026-09-08. Glen paid and could
# not tell whether it had worked.
#
# We already build a notification from this payload and send it inward. This
# sends the same facts outward, in the buyer's language.
#
# What it must never say is that payment succeeded. The GrooveKart webhook
# signals order CREATION and carries no settlement field, so at the moment this
# fires we do not know whether the card cleared. Saying "payment received" here
# would be a guess dressed as a receipt.


def _money(value):
    try:
        return "$%,.2f".replace(",", "") % float(value or 0)
    except (TypeError, ValueError):
        return "$0.00"


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(value):
    return str(value).strip() if value not in (None, "") else ""


def _customer(p):
    name = " ".join(x for x in (_text(p.get("customer_firstname")),
                                _text(p.get("customer_lastname"))) if x)
    return name or _text(p.get("customer_email")) or "(unnamed customer)"


def _lines(p):
    """'2 x Lipid Cleanse' per product. Quantity first: it is the thing most
    easily got wrong when packing."""
    out = []
    for item in (p.get("products") if isinstance(p.get("products"), list) else []):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("product_name")) or _text(item.get("name")) or "(unnamed product)"
        sku = _text(item.get("product_reference")) or _text(item.get("reference"))
        try:
            qty = int(float(item.get("product_quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        out.append("  %d x %s%s" % (qty, name, ("   [%s]" % sku) if sku else ""))
    return out or ["  (no product detail in the notification)"]


def _address(p):
    d = p.get("delivery") if isinstance(p.get("delivery"), dict) else {}
    if not d:
        return ["  (no shipping address in the notification)"]
    who = " ".join(x for x in (_text(d.get("firstname")), _text(d.get("lastname"))) if x)
    city = ", ".join(x for x in (_text(d.get("city")), _text(d.get("state_name"))) if x)
    # GrooveKart sends `address` and `phone_mobile`. The PrestaShop-style
    # `address1`/`address2`/`phone` are accepted too, because a fixture written
    # from those names once produced an email with no street on it at all.
    street = _text(d.get("address")) or _text(d.get("address1"))
    rows = [who, street, _text(d.get("address2")),
            " ".join(x for x in (city, _text(d.get("postcode"))) if x),
            _text(d.get("country")),
            _text(d.get("phone_mobile")) or _text(d.get("phone"))]
    return ["  " + r for r in rows if r] or ["  (no shipping address)"]


def order_email(payload):
    """(subject, body) for one store order. Never raises."""
    p = payload if isinstance(payload, dict) else {}
    ref = _text(p.get("id")) or _text(p.get("reference")) or "?"
    who = _customer(p)
    paid = _num(p.get("total_paid"))
    subject = "New store order %s: %s, %s" % (ref, who, _money(paid))

    body = ["A new order came in on remedymatch.com.", ""]
    body.append("Order %s%s" % (ref, ("  (%s)" % _text(p.get("reference")))
                                if _text(p.get("reference")) else ""))
    if _text(p.get("date_add")):
        body.append("Placed %s" % _text(p.get("date_add")))
    body += ["", "%s <%s>" % (who, _text(p.get("customer_email")) or "no email"), "", "Ordered:"]
    body += _lines(p)
    body += ["", "Ship to:"]
    body += _address(p)
    if _text(p.get("carrier_name")):
        body.append("  via %s" % _text(p.get("carrier_name")))

    body += ["", "Products   %s" % _money(p.get("total_products"))]
    if _num(p.get("total_discounts")):
        body.append("Discount  -%s" % _money(p.get("total_discounts")))
    body.append("Shipping   %s" % _money(p.get("total_shipping")))
    body.append("Total      %s   (%s)" % (_money(paid),
                                          _text(p.get("payment")) or "payment method unknown"))
    body += ["",
             "You are getting this from Remedy Match rather than from GrooveKart: "
             "their mail service has been refusing delivery to support@remedymatch.com "
             "since 28 August, so their own order notification is not arriving. This "
             "stands in for it until the domain moves to Google Workspace.",
             "",
             "The order is on the board at https://illtowell.com/console/orders"]
    return subject, "\n".join(body)


def buyer_email(payload):
    """(subject, body) confirming the order to the person who placed it.

    Never raises, like everything else here: this runs inside the webhook, and
    an exception would return 500 and start a GrooveKart retry storm.

    Deliberately says nothing about payment. See the note beside RECIPIENTS."""
    p = payload if isinstance(payload, dict) else {}
    ref = _text(p.get("reference")) or _text(p.get("id")) or "?"
    first = _text(p.get("customer_firstname"))

    subject = "We have your Remedy Match order %s" % ref

    body = ["%s," % (first or "Hello"), "",
            "Your order reached us. Here is what it says.", "",
            "Order %s" % ref]
    if _text(p.get("date_add")):
        body.append("Placed %s" % _text(p.get("date_add")))
    body += ["", "You ordered:"]
    # _lines() carries the SKU, which is there so Rae can pack the right jar.
    # A customer has no use for "[CLED-5ML]" and it makes the email read like a
    # warehouse slip, so strip it for them.
    body += [_re.sub(r"\s*\[[^\]]+\]\s*$", "", ln) for ln in _lines(p)]

    d = p.get("delivery") if isinstance(p.get("delivery"), dict) else {}
    if d:
        body += ["", "Going to:"]
        body += _address(p)

    body += ["", "Total %s" % _money(_num(p.get("total_paid")))]
    body += ["",
             "If your card was charged you will see it from Remedy Match. If you "
             "are not sure whether it went through, reply to this email before "
             "ordering again and we will check rather than risk charging you twice.",
             "",
             "Questions about the order go to Support@RemedyMatch.com or "
             "808-217-9647 after 1 PM EST.",
             "",
             "Remedy Match"]
    return subject, "\n".join(body)


def buyer_address(payload):
    """The buyer's email, or "" when the payload has none worth sending to.

    Returned separately so the caller decides whether to send. An empty string
    is normal: some orders arrive without one, and that is not an error."""
    p = payload if isinstance(payload, dict) else {}
    return _text(p.get("customer_email"))
