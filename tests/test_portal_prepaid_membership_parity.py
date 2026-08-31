import sqlite3
from datetime import datetime, timedelta
from unittest import mock

from dashboard import portal_view


def _membership_db():
    cx = sqlite3.connect(":memory:")
    cx.execute(
        "CREATE TABLE memberships (email TEXT, source TEXT, granted_at TEXT, "
        "expires_at TEXT)"
    )
    now = datetime.utcnow()
    cx.execute(
        "INSERT INTO memberships VALUES (?,?,?,?)",
        (
            "prepaid@example.com",
            "prepay_12mo",
            now.isoformat() + "Z",
            (now + timedelta(days=365)).isoformat() + "Z",
        ),
    )
    return cx


def test_paid_prepaid_grant_gets_active_membership_summary():
    cx = _membership_db()
    block = portal_view._membership_block(
        cx, "prepaid@example.com", paid_member=True
    )
    assert block["status"] == "Active"
    assert block["level"] == "Healing Oasis Membership"
    assert "paid membership" in block["detail"]


def test_journey_member_uses_authoritative_paid_member_verdict():
    with mock.patch(
        "dashboard.portal_onboarding.build_status",
        return_value={"phases": [], "member": False},
    ):
        status = portal_view._journey_block(
            sqlite3.connect(":memory:"),
            "prepaid@example.com",
            paid_member=True,
        )
    assert status["member"] is True

