# tests/test_biofield_month_on_delivery.py
"""The Biofield month runs from RECEIPT OF REMEDIES, not from payment.

Glen 2026-08-27: a $300 buyer gets immediate access at payment (that grant is
_grant_biofield_line_on_paid, live since #1369) AND the 30-day month starts when
their first remedy order is delivered -- so they get somewhat over a month.

Before this, the 30 days started at payment. Since the prerequisites (photo,
intake form, voice scan, biofield report) all clear before remedies ship, a
client could burn most or all of the month before anything arrived. Anyone who
bought between 2026-08-15 and this change was short-changed.

A full member pays $200 precisely BECAUSE they have already paid for that month,
so a $200 line earns no time at all.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    return appmod


def _cx(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    from dashboard.orders import init_orders_table
    init_orders_table(cx)
    cx.execute("CREATE TABLE IF NOT EXISTS memberships (id TEXT PRIMARY KEY, email TEXT, "
               "granted_at TEXT, expires_at TEXT, granted_by TEXT, source TEXT, "
               "truly_vip_ref TEXT, notes TEXT)")
    cx.commit()
    return cx


def _order(appmod, cx, *, email, items, days_ago=1, status="paid", ref=None):
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z"
    cx.execute("INSERT INTO orders (created_at, source, external_ref, channel, email, "
               "items_json, total_cents, status) VALUES (?,?,?,?,?,?,?,?)",
               (created, "portal", ref or f"o-{email}-{days_ago}", "retail", email,
                json.dumps(items), 30000, status))
    cx.commit()
    from dashboard import orders as _o
    cx.row_factory = sqlite3.Row
    got = [o for o in _o.list_orders_by_email(cx, email) if o.get("external_ref") == (ref or f"o-{email}-{days_ago}")]
    cx.row_factory = None
    return got[0]


BIOFIELD_300 = [{"slug": "biofield-analysis", "qty": 1, "unit_cents": 30000}]
BIOFIELD_200 = [{"slug": "biofield-analysis", "qty": 1, "unit_cents": 20000}]
REMEDIES = [{"slug": "terrain-restore", "qty": 1, "unit_cents": 6997}]


def _expiry(appmod, cx, email):
    row = cx.execute("SELECT expires_at FROM memberships WHERE email=? "
                     "ORDER BY expires_at DESC LIMIT 1", (email,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------

def test_delivery_of_remedies_starts_the_month(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "buyer@example.com"
    _order(appmod, cx, email=email, items=BIOFIELD_300, days_ago=20, ref="bf-1")
    delivered = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-1")

    assert appmod._extend_biofield_month_on_delivery(cx, email, delivered) == "granted"
    exp = _expiry(appmod, cx, email)
    assert exp, "no membership row written"
    days = (datetime.fromisoformat(exp.replace("Z", "")) - datetime.utcnow()).days
    assert 28 <= days <= 30, f"expected ~30 days from delivery, got {days}"


def test_a_service_only_delivery_does_not_start_the_month(appmod, monkeypatch):
    """The Biofield order ships nothing. Its own 'delivery' must not start the
    clock -- the month runs from receipt of REMEDIES."""
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "serviceonly@example.com"
    bf = _order(appmod, cx, email=email, items=BIOFIELD_300, days_ago=5, ref="bf-2")
    assert appmod._extend_biofield_month_on_delivery(cx, email, bf) == "none"
    assert _expiry(appmod, cx, email) is None


def test_a_200_member_line_earns_no_month(appmod, monkeypatch):
    """The $100 difference IS the month. A member already paid for it."""
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "fullmember@example.com"
    _order(appmod, cx, email=email, items=BIOFIELD_200, days_ago=20, ref="bf-3")
    delivered = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-3")
    assert appmod._extend_biofield_month_on_delivery(cx, email, delivered) == "none"
    assert _expiry(appmod, cx, email) is None


def test_it_extends_existing_access_instead_of_being_swallowed(appmod, monkeypatch):
    """THE SUBTLE ONE. _grant_membership writes `now + days`, and the read path
    takes the FURTHEST expiry. Granting 'now + 30' to somebody who already holds
    40 days is a silent no-op -- the month vanishes. It must compute from the end
    of current access."""
    cx = _cx(appmod)
    email = "hasaccess@example.com"
    existing_end = datetime.utcnow() + timedelta(days=40)
    monkeypatch.setattr(appmod, "_active_membership_for_email",
                        lambda e: {"expires_at": existing_end.isoformat() + "Z",
                                   "lifetime": False})
    _order(appmod, cx, email=email, items=BIOFIELD_300, days_ago=20, ref="bf-4")
    delivered = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-4")

    assert appmod._extend_biofield_month_on_delivery(cx, email, delivered) == "granted"
    exp = _expiry(appmod, cx, email)
    days = (datetime.fromisoformat(exp.replace("Z", "")) - datetime.utcnow()).days
    assert days >= 68, f"expected ~70 days (40 existing + 30), got {days} -- the month was swallowed"


def test_one_month_per_purchase_however_many_parcels(appmod, monkeypatch):
    """Idempotent per BIOFIELD order, not per delivery. A split shipment must not
    hand out two months.

    TWO mechanisms enforce this, deliberately: _qualifying_biofield_order skips
    purchases that already hold a claim row, AND the claim INSERT uses ON
    CONFLICT DO NOTHING. Removing either one alone leaves this test green -- that
    is redundancy, not a sleeping guard. The second is the real race guard for
    two parcels settling at the same instant, which the first cannot cover."""
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "twoparcels@example.com"
    _order(appmod, cx, email=email, items=BIOFIELD_300, days_ago=20, ref="bf-5")
    d1 = _order(appmod, cx, email=email, items=REMEDIES, days_ago=2, ref="rem-5a")
    d2 = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-5b")
    assert appmod._extend_biofield_month_on_delivery(cx, email, d1) == "granted"
    # The second delivery reports "none" rather than "already": _qualifying_
    # biofield_order already filters out purchases holding a claim row, so by the
    # time the second parcel lands there is simply nothing left to grant. Either
    # string is fine; what must hold is that no SECOND month is written, so that
    # is what this asserts rather than the status text.
    assert appmod._extend_biofield_month_on_delivery(cx, email, d2) != "granted"
    n = cx.execute("SELECT COUNT(*) FROM memberships WHERE email=?", (email,)).fetchone()[0]
    assert n == 1, f"{n} membership rows for one purchase"


def test_no_biofield_purchase_means_no_grant(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "justremedies@example.com"
    delivered = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-6")
    assert appmod._extend_biofield_month_on_delivery(cx, email, delivered) == "none"


def test_a_cancelled_biofield_order_earns_nothing(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    cx = _cx(appmod)
    email = "cancelled@example.com"
    _order(appmod, cx, email=email, items=BIOFIELD_300, days_ago=20, ref="bf-7", status="cancelled")
    delivered = _order(appmod, cx, email=email, items=REMEDIES, days_ago=1, ref="rem-7")
    assert appmod._extend_biofield_month_on_delivery(cx, email, delivered) == "none"


def test_it_never_raises_into_the_delivery_path(appmod):
    """A grant failure must not stop a shipment being marked delivered."""
    cx = sqlite3.connect(":memory:")   # no orders table at all
    assert appmod._extend_biofield_month_on_delivery(cx, "x@example.com", {"items": []}) in ("none", "error")


def test_every_delivery_path_reaches_it():
    """Carrier status and both manual mark-delivered routes all funnel through
    _activate_coaching_for_shipment, so the hook belongs there and nowhere else."""
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)
    callers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               for sub in ast.walk(n)
               if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
               and sub.func.id == "_extend_biofield_month_on_delivery"}
    assert callers == {"_activate_coaching_for_shipment"}, callers
