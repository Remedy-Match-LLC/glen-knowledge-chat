"""Conservative cleanup of obsolete app-created QBO SalesReceipts."""

import re


_NUMERIC = re.compile(r"^[0-9]+$")


def _account_name(account):
    return (account or {}).get("FullyQualifiedName") or (account or {}).get("Name") or ""


def _is_bank_of_hawaii(account):
    name = " ".join(_account_name(account).lower().replace("&", "and").split())
    return "bank of hawaii" in name


def audit(cx, qbo):
    """Return deletable and blocked historical receipt candidates."""
    rows = cx.execute(
        "SELECT id, external_ref, email, total_cents, qbo_sales_receipt_id "
        "FROM orders WHERE qbo_sales_receipt_id IS NOT NULL ORDER BY id"
    ).fetchall()
    results = []
    account_cache = {}
    for raw in rows:
        order = dict(raw)
        rid = str(order.get("qbo_sales_receipt_id") or "").strip()
        item = {"order_id": order.get("id"), "external_ref": order.get("external_ref"),
                "email": order.get("email"), "total_cents": order.get("total_cents"),
                "qbo_sales_receipt_id": rid, "deletable": False, "reasons": []}
        if rid.startswith("DELETED:"):
            item["status"] = "already_deleted"
            results.append(item)
            continue
        if not _NUMERIC.fullmatch(rid):
            item["status"] = "blocked"
            item["reasons"].append("local receipt id is not numeric")
            results.append(item)
            continue
        try:
            receipt = qbo.get_sales_receipt(rid)
        except Exception as exc:
            item["status"] = "blocked"
            item["reasons"].append(f"QBO fetch failed: {exc}")
            results.append(item)
            continue
        if not receipt:
            item["status"] = "blocked"
            item["reasons"].append("QBO receipt not found")
            results.append(item)
            continue
        note = str(receipt.get("PrivateNote") or "").strip()
        expected_note = f"order:{order.get('external_ref')}"
        account_id = str((receipt.get("DepositToAccountRef") or {}).get("value") or "")
        if account_id not in account_cache:
            try:
                account_cache[account_id] = qbo.get_account(account_id) if account_id else None
            except Exception as exc:
                account_cache[account_id] = {"_fetch_error": str(exc)}
        account = account_cache.get(account_id)
        item.update({"qbo_doc_number": receipt.get("DocNumber"),
                     "qbo_total": receipt.get("TotalAmt"), "qbo_private_note": note,
                     "qbo_account_id": account_id, "qbo_account_name": _account_name(account)})
        if note != expected_note:
            item["reasons"].append("PrivateNote does not exactly identify this order")
        if account and account.get("_fetch_error"):
            item["reasons"].append(f"account fetch failed: {account['_fetch_error']}")
        elif not _is_bank_of_hawaii(account):
            item["reasons"].append("deposit account is not verified as Bank of Hawaii")
        expected_total = round(int(order.get("total_cents") or 0) / 100.0, 2)
        try:
            qbo_total = round(float(receipt.get("TotalAmt") or 0), 2)
        except (TypeError, ValueError):
            qbo_total = None
        if qbo_total != expected_total:
            item["reasons"].append("QBO total does not equal the in-house order total")
        item["deletable"] = not item["reasons"]
        item["status"] = "ready" if item["deletable"] else "blocked"
        results.append(item)
    return results


def delete_confirmed(cx, qbo, receipt_ids):
    """Delete an explicitly selected subset after re-auditing live state."""
    requested = {str(x).strip() for x in receipt_ids}
    current = audit(cx, qbo)
    ready = {x["qbo_sales_receipt_id"]: x for x in current if x["deletable"]}
    unknown = sorted(requested - set(ready))
    if unknown:
        raise ValueError("not currently safe/deletable: " + ", ".join(unknown))
    deleted = []
    for rid in sorted(requested, key=int):
        item = ready[rid]
        qbo.delete_sales_receipt(rid)
        cx.execute("UPDATE orders SET qbo_sales_receipt_id=? "
                   "WHERE id=? AND qbo_sales_receipt_id=?",
                   (f"DELETED:{rid}", item["order_id"], rid))
        cx.commit()
        deleted.append({"order_id": item["order_id"], "qbo_sales_receipt_id": rid})
    return deleted
