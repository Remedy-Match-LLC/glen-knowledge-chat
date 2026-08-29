"""Strict Bank of America Zelle notification reconciliation.

Only exact, unique matches are posted. Anything ambiguous stays unread and is
reported for operator review; the importer never guesses where money belongs.
"""
import re
import sqlite3
from datetime import datetime, timezone

from dashboard import db, order_payments, orders


SENDER = "customerservice@ealerts.bankofamerica.com"
PROCESSED_LABEL = "AMG_ZELLE_PROCESSED"
REVIEW_LABEL = "AMG_ZELLE_REVIEW"
SUBJECT_RE = re.compile(
    r"^\s*(?P<payer>.+?)\s+sent you\s+\$(?P<amount>[0-9,]+(?:\.\d{2})?)\s*$",
    re.IGNORECASE,
)


def parse_subject(subject):
    match = SUBJECT_RE.match(subject or "")
    if not match:
        return None
    cents = round(float(match.group("amount").replace(",", "")) * 100)
    return {"payer": " ".join(match.group("payer").split()), "amount_cents": cents}


def _name(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())


def _matching_order(cx, payer, amount_cents):
    """Return one safe match, or (None, reason).

    Prefer an exact payer-name invoice. If none exists, permit a household-name
    match only when the surname is non-trivial and exactly one open invoice has
    that surname *and* the exact remaining balance. This handles a spouse paying
    a spouse's invoice without making amount-only matches across customers.
    """
    payer_norm = _name(payer)
    surname = payer_norm.split()[-1] if payer_norm else ""
    rows = orders.list_orders(cx, limit=500)
    candidates = []
    for order in rows:
        if order.get("status") in orders._TERMINAL_STATUSES or order.get("pay_status") == "paid":
            continue
        bal = order_payments.balance(cx, order["id"])
        if bal["balance_cents"] != amount_cents:
            continue
        order_norm = _name(order.get("name"))
        if order_norm == payer_norm:
            candidates.append((0, order, bal))
        elif len(surname) >= 4 and order_norm.split()[-1:] == [surname]:
            candidates.append((1, order, bal))
    if not candidates:
        return None, "no exact open-balance match"
    best_rank = min(c[0] for c in candidates)
    best = [c for c in candidates if c[0] == best_rank]
    if len(best) != 1:
        return None, "multiple exact open-balance matches"
    return best[0][1], None


def _header(msg, key):
    for item in msg.get("payload", {}).get("headers", []) or []:
        if item.get("name", "").lower() == key.lower():
            return item.get("value", "")
    return ""


def process_notifications(svc, db_path, *, dry_run=False, max_messages=50):
    processed_id = _ensure_label(svc, PROCESSED_LABEL)
    review_id = _ensure_label(svc, REVIEW_LABEL)
    query = (f'in:inbox from:{SENDER} subject:"sent you $" '
             f'-label:{PROCESSED_LABEL} -label:{REVIEW_LABEL} newer_than:30d')
    found = svc.users().messages().list(
        userId="me", q=query, maxResults=max_messages).execute().get("messages", [])
    out = {"zelle_applied": 0, "zelle_review": 0, "zelle_errored": 0, "zelle_details": []}
    for stub in found:
        mid = stub["id"]
        try:
            msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
            sender = _header(msg, "From").lower()
            parsed = parse_subject(_header(msg, "Subject"))
            if SENDER not in sender or not parsed:
                raise ValueError("notification sender or subject did not pass validation")
            with db.connect(db_path) as cx:
                cx.row_factory = sqlite3.Row
                orders.init_orders_table(cx)
                order_payments.ensure_table(cx)
                order, reason = _matching_order(cx, parsed["payer"], parsed["amount_cents"])
                if not order:
                    out["zelle_review"] += 1
                    out["zelle_details"].append({"msg_id": mid, "action": "review", "reason": reason})
                    if not dry_run:
                        svc.users().messages().modify(
                            userId="me", id=mid, body={"addLabelIds": [review_id]}).execute()
                    continue
                external_ref = f"zelle:gmail:{mid}"
                if not dry_run:
                    paid_ms = int(msg.get("internalDate") or 0)
                    paid_at = (datetime.fromtimestamp(paid_ms / 1000, timezone.utc).isoformat()
                               if paid_ms else None)
                    order_payments.add_payment(
                        cx, order["id"], parsed["amount_cents"], "Zelle",
                        source="bank-email", external_ref=external_ref,
                        paid_at=paid_at,
                        note=f"Bank of America notification: {parsed['payer']}",
                        actor="zelle-email-import")
                    svc.users().messages().modify(
                        userId="me", id=mid,
                        body={"addLabelIds": [processed_id], "removeLabelIds": ["UNREAD"]},
                    ).execute()
                out["zelle_applied"] += 1
                out["zelle_details"].append(
                    {"msg_id": mid, "action": "applied", "order_id": order["id"],
                     "amount_cents": parsed["amount_cents"]})
        except Exception as exc:
            out["zelle_errored"] += 1
            out["zelle_details"].append({"msg_id": mid, "action": "error", "error": str(exc)})
    return out


def _ensure_label(svc, name):
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == name:
            return label["id"]
    return svc.users().labels().create(
        userId="me", body={"name": name, "labelListVisibility": "labelHide",
                           "messageListVisibility": "show"}).execute()["id"]
