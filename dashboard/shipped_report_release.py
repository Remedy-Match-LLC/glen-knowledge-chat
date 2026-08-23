"""Release approved biofield reports to a client's portal after shipment.

Clinical approval remains authoritative: this module never promotes a draft.  It
only makes an already-confirmed per-scan report reachable from a portal once an
order for that client reaches a shipped (or later) state.
"""
import json

from . import client_portal as portals
from . import portal_biofield_reports as reports


SHIPPED_STATUSES = {"shipped", "delivered", "done"}


def _order(cx, order_id):
    row = cx.execute(
        "SELECT id,email,name,status,items_json FROM orders WHERE id=?", (order_id,)
    ).fetchone()
    if not row:
        return None
    keys = ("id", "email", "name", "status", "items_json")
    return dict(row) if hasattr(row, "keys") else dict(zip(keys, row))


def _has_products(order):
    """A report is released by a physical-product shipment, not a service-only row."""
    try:
        items = json.loads(order.get("items_json") or "[]")
    except (TypeError, ValueError):
        return False
    return any(
        int(item.get("qty") or 0) > 0
        and (item.get("slug") or "").strip() != "biofield-analysis"
        and not item.get("service")
        for item in items
        if isinstance(item, dict)
    )


def release_for_order(cx, order_id, *, dry_run=False):
    """Ensure the client's latest approved report has a portal after shipment.

    Existing portal scan pins and opt-outs are respected.  New/bare portals point
    at the latest confirmed report.  Returns an audit-friendly result and is
    idempotent.
    """
    order = _order(cx, order_id)
    if not order:
        return {"released": False, "reason": "order-not-found", "order_id": order_id}
    if order.get("status") not in SHIPPED_STATUSES:
        return {"released": False, "reason": "not-shipped", "order_id": order_id}
    if not _has_products(order):
        return {"released": False, "reason": "no-physical-products", "order_id": order_id}
    email = (order.get("email") or "").strip().lower()
    if not email:
        return {"released": False, "reason": "missing-email", "order_id": order_id}

    portals.init_client_portal_table(cx)
    reports.init_table(cx)
    rep = cx.execute(
        "SELECT scan_date,content_json FROM portal_biofield_reports "
        "WHERE lower(email)=? AND status='confirmed' ORDER BY scan_date DESC LIMIT 1",
        (email,),
    ).fetchone()
    if not rep:
        return {"released": False, "reason": "no-confirmed-report", "order_id": order_id,
                "email": email}
    scan_date, content_json = rep[0], rep[1]
    existing = portals.get_portal_content_by_email(cx, email)
    current = portals.get_current_scan(cx, email) if existing else None
    auto_advance = portals.get_auto_advance(cx, email) if existing else True
    changed = not existing or (auto_advance and current != scan_date)
    if dry_run:
        return {"released": changed, "dry_run": True, "order_id": order_id,
                "email": email, "scan_date": scan_date}

    if not existing:
        try:
            content = json.loads(content_json or "{}")
        except (TypeError, ValueError):
            content = {}
        portals.upsert_portal(cx, email, order.get("name") or "", content)
    portals.set_biofield_status(cx, email, "confirmed")
    if auto_advance:
        portals.set_current_scan(cx, email, scan_date)
    cx.commit()
    return {"released": changed, "order_id": order_id, "email": email,
            "scan_date": scan_date}


def backfill(cx, *, dry_run=True):
    """Catch up shipped clients, once per email (newest shipped order wins)."""
    rows = cx.execute(
        "SELECT id FROM orders o WHERE status IN ('shipped','delivered','done') "
        "AND email IS NOT NULL AND TRIM(email)<>'' "
        "AND id=(SELECT MAX(id) FROM orders n WHERE lower(n.email)=lower(o.email) "
        "AND n.status IN ('shipped','delivered','done')) ORDER BY id"
    ).fetchall()
    results = [release_for_order(cx, row[0], dry_run=dry_run) for row in rows]
    return {
        "scanned": len(results),
        "released": sum(bool(r.get("released")) for r in results),
        "reasons": {reason: sum(r.get("reason") == reason for r in results)
                    for reason in sorted({r.get("reason") for r in results if r.get("reason")})},
        "results": results,
    }
