"""Paid certification students, and paid Biofield clients, get mix/match (Glen, 2026-10-03).

Agnes Verches paid for her certification enrollment and for a Biofield Analysis, and her
remedy invoice still showed full prices. Glen: "Both/each should trigger mixed quantity
pricing discount." And on the enrollment: "every one should show as a paid member
through the end of the year."

Round 1 of the first version found its signal self-assignable: anyone who registered as
a coach became a paid member. Only a PAYMENT counts now, read from local records.
"""
import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

AGNES = "agnes@example.com"
OTHER = "someone@example.com"


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_active_membership_for_email", lambda e: None)
    monkeypatch.setattr(mod, "_family_plan_enabled", lambda: False)
    monkeypatch.setattr(mod, "_canonical_email", lambda e: (e or "").strip().lower())
    monkeypatch.setattr(mod, "_cert_member_window_open", lambda today=None: True)
    cx = sqlite3.connect(str(mod.LOG_DB))
    cx.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, email TEXT, "
               "status TEXT, pay_status TEXT, paid_at TEXT, items_json TEXT)")
    cx.commit()
    cx.close()
    return mod


def _order(a, email, slugs, *, pay="paid", status="proposed", paid_at=None):
    paid_at = paid_at or datetime.now(timezone.utc).isoformat()
    row = {"email": email, "status": status, "pay_status": pay, "paid_at": paid_at,
           "items_json": json.dumps([{"slug": s, "qty": 1} for s in slugs])}
    cx = sqlite3.connect(str(a.LOG_DB))
    # The app's own orders table has more required columns; give each a filler.
    for _cid, name, typ, notnull, default, pk in cx.execute("PRAGMA table_info(orders)"):
        if notnull and default is None and not pk and name not in row:
            row[name] = 0 if "INT" in (typ or "").upper() else "x"
    import uuid
    row["external_ref"] = "T-" + uuid.uuid4().hex[:8]   # unique with source
    cols = ",".join(row)
    cx.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(row))})",
               tuple(row.values()))
    cx.commit()
    cx.close()


def _ffs(a, n=3):
    P = a._PRODUCTS["products"]
    out = [s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
           and not p.get("inactive") and not p.get("info_only")
           and int(p.get("price_cents") or 0) == 6997][:n]
    assert len(out) == n
    return out


def _invoice_subtotal(a, email):
    lines = [{"slug": s, "qty": 1} for s in _ffs(a)]
    ship = {"name": "A", "street": "1 A St", "address2": "", "city": "X", "state": "MA",
            "zip": "01950", "country": "US"}
    return a._price_inhouse_invoice(lines, email=email, pickup=True, ship=ship)["subtotal_cents"]


# --- paid certification enrollment ---

@pytest.mark.parametrize("slug", ["ash-practitioner-cert", "ash-practitioner-cert-monthly"])
def test_a_paid_enrollment_order_makes_a_paid_member(a, slug):
    _order(a, AGNES, [slug])
    assert a._is_paid_member(AGNES)
    assert not a._is_paid_member(OTHER)


def test_an_unpaid_or_cancelled_enrollment_does_not(a):
    _order(a, AGNES, ["ash-practitioner-cert"], pay="unpaid")
    _order(a, AGNES, ["ash-practitioner-cert"], status="cancelled")
    assert not a._is_paid_member(AGNES)


def test_a_stripe_course_entitlement_counts(a):
    from dashboard import course_entitlements as ce
    cx = sqlite3.connect(str(a.LOG_DB))
    ce.init_course_entitlements_table(cx)
    cx.execute("INSERT INTO course_entitlements(email,kind,status,source) "
               "VALUES(?,?,?,?)", (AGNES, "cert_onetime", "active", "stripe"))
    cx.commit()
    cx.close()
    assert a._is_paid_member(AGNES)


def test_a_near_name_slug_is_not_an_enrollment(a):
    _order(a, AGNES, ["ash-practitioner-cert-info"])
    assert not a._is_paid_member(AGNES)


def test_the_window_closes_after_december_31(a, monkeypatch):
    importlib.reload(a)   # the real window function, not the fixture's stub
    assert a._cert_member_window_open("2026-12-31")
    assert not a._cert_member_window_open("2027-01-01")


def test_a_paid_student_gets_the_member_biofield_price(a):
    _order(a, AGNES, ["ash-practitioner-cert"])
    p = {"price_cents": 30000, "member_price_cents": 20000}
    assert a._member_price_cents(p, AGNES) == 20000
    assert a._member_price_cents(p, OTHER) is None


def test_a_paid_student_gets_mix_and_match_on_the_invoice(a):
    _order(a, AGNES, ["ash-practitioner-cert"])
    assert _invoice_subtotal(a, AGNES) < _invoice_subtotal(a, OTHER)


# --- paid Biofield Analysis ---

def test_a_biofield_paid_this_month_gives_mix_and_match(a):
    _order(a, AGNES, ["biofield-analysis"],
           paid_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    assert a._mix_match_member(AGNES)
    assert _invoice_subtotal(a, AGNES) < _invoice_subtotal(a, OTHER)


def test_a_paid_biofield_alone_is_not_full_membership(a):
    """The $200 member Biofield price is the included month itself, so it stays on
    real membership."""
    _order(a, AGNES, ["biofield-analysis"])
    assert not a._is_paid_member(AGNES)
    assert a._member_price_cents({"price_cents": 30000, "member_price_cents": 20000},
                                 AGNES) is None


@pytest.mark.parametrize("kw", [
    {"paid_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()},
    {"pay": "unpaid"},
    {"status": "cancelled"},
    {"paid_at": "not a date"},
])
def test_an_old_unpaid_cancelled_or_undated_biofield_does_not(a, kw):
    _order(a, AGNES, ["biofield-analysis"], **kw)
    assert not a._mix_match_member(AGNES)
    assert _invoice_subtotal(a, AGNES) == _invoice_subtotal(a, OTHER)


def test_a_space_separated_naive_timestamp_reads(a):
    t = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _order(a, AGNES, ["biofield-analysis"], paid_at=t)
    assert a._mix_match_member(AGNES)


def test_an_unreadable_orders_table_fails_closed(a, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(a.db, "connect", boom)
    assert not a._paid_cert_student(AGNES)
    assert not a._biofield_paid_within(AGNES)
