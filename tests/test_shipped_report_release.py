import json
import sqlite3

from dashboard import client_portal as portals
from dashboard import orders
from dashboard import portal_biofield_reports as reports
from dashboard import shipped_report_release as release


def _db():
    cx = sqlite3.connect(":memory:")
    orders.init_orders_table(cx)
    reports.init_table(cx)
    portals.init_client_portal_table(cx)
    return cx


def _order(cx, email, status="new", items=None, name="Client"):
    return orders.upsert_order(
        cx, source="biofield", external_ref=f"x-{email}", email=email, name=name,
        status=status, items=items or [{"slug": "liver-support", "qty": 1}],
    )


def test_shipping_creates_portal_for_latest_confirmed_report():
    cx = _db()
    oid = _order(cx, "member@symons.test", name="Symons Member")
    reports.upsert_report(cx, "member@symons.test", "2026-08-01", "s1",
                          {"greeting": "approved", "layers": [{"title": "L1"}]}, "confirmed")
    assert portals.get_portal_content_by_email(cx, "member@symons.test") is None

    orders.set_order_status(cx, oid, "shipped")

    portal = portals.get_portal_content_by_email(cx, "member@symons.test")
    assert portal is not None
    assert portals.get_current_scan(cx, "member@symons.test") == "2026-08-01"


def test_shipping_never_publishes_unapproved_draft():
    cx = _db()
    oid = _order(cx, "draft@symons.test")
    reports.upsert_report(cx, "draft@symons.test", "2026-08-02", "s2",
                          {"greeting": "draft"}, "ai_draft")
    orders.set_order_status(cx, oid, "shipped")
    assert portals.get_portal_content_by_email(cx, "draft@symons.test") is None


def test_service_only_order_does_not_release_report():
    cx = _db()
    oid = _order(cx, "service@x.test", items=[{"slug": "biofield-analysis", "qty": 1}])
    reports.upsert_report(cx, "service@x.test", "2026-08-03", "s3", {"layers": []}, "confirmed")
    orders.set_order_status(cx, oid, "shipped")
    assert portals.get_portal_content_by_email(cx, "service@x.test") is None


def test_existing_opted_out_portal_keeps_its_pinned_report():
    cx = _db()
    oid = _order(cx, "pin@symons.test")
    portals.upsert_portal(cx, "pin@symons.test", "Pinned", {"greeting": "old"})
    reports.upsert_report(cx, "pin@symons.test", "2026-07-01", "s0", {"layers": []}, "confirmed")
    reports.upsert_report(cx, "pin@symons.test", "2026-08-01", "s1", {"layers": []}, "confirmed")
    portals.set_current_scan(cx, "pin@symons.test", "2026-07-01")
    portals.set_auto_advance(cx, "pin@symons.test", False)
    orders.set_order_status(cx, oid, "shipped")
    assert portals.get_current_scan(cx, "pin@symons.test") == "2026-07-01"


def test_backfill_catches_each_shipped_family_member():
    cx = _db()
    for email in ("one@symons.test", "two@symons.test"):
        _order(cx, email, status="shipped")
        reports.upsert_report(cx, email, "2026-08-01", email, {"layers": []}, "confirmed")
    out = release.backfill(cx, dry_run=False)
    assert out["released"] == 2
    assert portals.get_portal_content_by_email(cx, "one@symons.test")
    assert portals.get_portal_content_by_email(cx, "two@symons.test")
