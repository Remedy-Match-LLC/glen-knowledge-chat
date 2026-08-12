"""Read-only, sanitized Stripe customer lookup for the owner Console."""

from urllib.parse import urlencode

from dashboard import stripe_pay


def _search_value(value: str) -> str:
    """Escape a value for Stripe's single-quoted search query syntax."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _get(path: str, params=None) -> dict:
    suffix = urlencode(params or {})
    return stripe_pay._get(path + (("?" + suffix) if suffix else ""))


def _customers(term: str, limit: int = 10) -> list[dict]:
    if "@" in term:
        searches = [("email", term)]
    else:
        # Searching the useful name tokens also handles ordinary variations such
        # as "Ann Metzen" versus Stripe's "Anne S Metzen".
        values = [term] + [part for part in reversed(term.split()) if len(part) >= 2]
        searches = [("name", value) for value in dict.fromkeys(values)]
    found = {}
    for field, value in searches:
        escaped = _search_value(value)
        result = _get("/customers/search", {
            "query": f"{field}~'{escaped}'",
            "limit": max(1, min(limit, 20)),
        })
        for customer in result.get("data") or []:
            if customer.get("id"):
                found[customer["id"]] = customer
            if len(found) >= limit:
                return list(found.values())[:limit]
    return list(found.values())


def _kind(obj: dict) -> str:
    metadata = obj.get("metadata") or {}
    return metadata.get("kind") or metadata.get("source") or ""


def _payment(charge: dict) -> dict:
    return {
        "id": charge.get("id"),
        "amount": charge.get("amount"),
        "currency": charge.get("currency"),
        "created": charge.get("created"),
        "status": charge.get("status"),
        "paid": bool(charge.get("paid")),
        "refunded": bool(charge.get("refunded")),
        "amount_refunded": charge.get("amount_refunded") or 0,
        "disputed": bool(charge.get("disputed")),
        "description": charge.get("description") or "",
        "kind": _kind(charge),
    }


def _invoice(invoice: dict) -> dict:
    return {
        "id": invoice.get("id"),
        "number": invoice.get("number"),
        "status": invoice.get("status"),
        "paid": bool(invoice.get("paid")),
        "amount_due": invoice.get("amount_due") or 0,
        "amount_paid": invoice.get("amount_paid") or 0,
        "amount_remaining": invoice.get("amount_remaining") or 0,
        "currency": invoice.get("currency"),
        "created": invoice.get("created"),
        "due_date": invoice.get("due_date"),
        "description": invoice.get("description") or "",
        "hosted_invoice_url": invoice.get("hosted_invoice_url") or "",
    }


def _subscription(subscription: dict) -> dict:
    items = ((subscription.get("items") or {}).get("data") or [])
    item = items[0] if items else {}
    price = item.get("price") or {}
    return {
        "id": subscription.get("id"),
        "status": subscription.get("status"),
        "created": subscription.get("created"),
        "current_period_end": subscription.get("current_period_end") or item.get("current_period_end"),
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")),
        "amount": price.get("unit_amount"),
        "currency": price.get("currency"),
        "interval": ((price.get("recurring") or {}).get("interval")),
        "kind": _kind(subscription),
    }


def lookup(term: str, customer_limit: int = 10) -> dict:
    """Find customers and return operational fields only; never card/bank data."""
    term = (term or "").strip()
    if len(term) < 2:
        raise ValueError("Enter at least 2 characters of a customer name or email")

    results = []
    for customer in _customers(term, customer_limit):
        customer_id = customer["id"]
        charges = _get("/charges", {"customer": customer_id, "limit": 25})
        invoices = _get("/invoices", {"customer": customer_id, "limit": 25})
        subscriptions = _get("/subscriptions", {
            "customer": customer_id, "status": "all", "limit": 25,
        })
        results.append({
            "customer": {
                "id": customer_id,
                "name": customer.get("name") or "",
                "email": customer.get("email") or "",
                "created": customer.get("created"),
                "delinquent": bool(customer.get("delinquent")),
            },
            "payments": [_payment(x) for x in charges.get("data") or []],
            "invoices": [_invoice(x) for x in invoices.get("data") or []],
            "subscriptions": [_subscription(x) for x in subscriptions.get("data") or []],
        })
    return {"query": term, "matches": results}
