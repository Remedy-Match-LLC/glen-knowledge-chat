"""Paid certification students, and paid Biofield clients, get mix/match (Glen, 2026-10-03).

Agnes Verches paid for her certification enrollment and for a Biofield Analysis, and her
remedy invoice still showed full prices. Glen: "Both/each should trigger mixed quantity
pricing discount." And on the enrollment: "every one should show as a paid member
through the end of the year."

Round 1 of the first version found its signal self-assignable: anyone who registered as
a coach became a paid member. Only a PAYMENT counts now, read from local records.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

AGNES = "agnes@example.com"
OTHER = "someone@example.com"


_REAL_WINDOW = None


@pytest.fixture
def a(monkeypatch, tmp_path):
    # Point the app's log database at a temp file. Reloading the module instead
    # leaked into later test files (round 1 run: test_membership_lookup).
    global _REAL_WINDOW
    import app as mod
    _REAL_WINDOW = _REAL_WINDOW or mod._cert_member_window_open
    monkeypatch.setattr(mod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "_PAYMENT_CACHE", {})
    monkeypatch.setattr(mod, "_active_membership_for_email", lambda e: None)
    monkeypatch.setattr(mod, "_family_plan_enabled", lambda: False)
    monkeypatch.setattr(mod, "_canonical_email", lambda e: (e or "").strip().lower())
    monkeypatch.setattr(mod, "_cert_member_window_open", lambda today=None: True)
    cx = sqlite3.connect(str(mod.LOG_DB))
    from dashboard import orders as _orders
    _orders.init_orders_table(cx)        # the app's real schema
    from dashboard import order_payments as _op
    _op.ensure_table(cx)                 # prod always has the ledger
    cx.commit()
    cx.close()
    return mod


def _order(a, email, slugs, *, pay="paid", status="proposed", paid_at=None, items=None):
    paid_at = paid_at or datetime.now(timezone.utc).isoformat()
    row = {"email": email, "status": status, "pay_status": pay, "paid_at": paid_at,
           "paid_cents": 30000, "total_cents": 30000,
           "items_json": json.dumps(items if items is not None
                                    else [{"slug": s, "qty": 1} for s in slugs])}
    cx = sqlite3.connect(str(a.LOG_DB))
    # The app's own orders table has more required columns; give each a filler.
    for _cid, name, typ, notnull, default, pk in cx.execute("PRAGMA table_info(orders)"):
        if notnull and default is None and not pk and name not in row:
            row[name] = 0 if "INT" in (typ or "").upper() else "x"
    import uuid
    row["external_ref"] = "T-" + uuid.uuid4().hex[:8]   # unique with source
    cols = ",".join(row)
    cur = cx.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(row))})",
                     tuple(row.values()))
    cx.commit()
    cx.close()
    return cur.lastrowid


def _ledger(a, oid, kind, cents):
    from dashboard import order_payments as op
    cx = sqlite3.connect(str(a.LOG_DB))
    cx.row_factory = sqlite3.Row
    op.ensure_table(cx)
    op._insert(cx, oid, kind=kind, amount_cents=cents, method="zelle", source="manual",
               external_ref=None, refunds_payment_id=None, paid_at=None, note=None,
               actor=None)
    cx.close()


def _ffs(a, n=3):
    P = a._PRODUCTS["products"]
    out = [s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
           and not p.get("inactive") and not p.get("info_only")
           and int(p.get("price_cents") or 0) == 7000][:n]
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


@pytest.mark.parametrize("source,counts", [("stripe", True),
                                           ("certification_roster", False),
                                           ("module_certification", False)])
def test_only_a_stripe_course_entitlement_counts(a, source, counts):
    """Round 1: the coach roster and the 12th module approval write cert rows with no
    payment. Only Stripe's are purchases."""
    from dashboard import course_entitlements as ce
    cx = sqlite3.connect(str(a.LOG_DB))
    ce.init_course_entitlements_table(cx)
    cx.execute("INSERT INTO course_entitlements(email,kind,status,source) "
               "VALUES(?,?,?,?)", (AGNES, "cert_onetime", "active", source))
    cx.commit()
    cx.close()
    assert a._is_paid_member(AGNES) is counts


def test_a_full_refund_cancels_the_enrollment(a):
    oid = _order(a, AGNES, ["ash-practitioner-cert"])
    _ledger(a, oid, "payment", 30000)
    _ledger(a, oid, "refund", 30000)
    assert not a._is_paid_member(AGNES)


def test_a_partial_refund_keeps_it(a):
    oid = _order(a, AGNES, ["ash-practitioner-cert"])
    _ledger(a, oid, "payment", 30000)
    _ledger(a, oid, "refund", 10000)
    assert a._is_paid_member(AGNES)


def test_a_monthly_installment_counts_for_35_days(a):
    old = (datetime.now(timezone.utc) - timedelta(days=36)).isoformat()
    _order(a, AGNES, ["ash-practitioner-cert-monthly"], paid_at=old)
    assert not a._is_paid_member(AGNES)
    a._PAYMENT_CACHE.clear()
    _order(a, AGNES, ["ash-practitioner-cert-monthly"],
           paid_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat())
    assert a._is_paid_member(AGNES)


def test_a_near_name_slug_is_not_an_enrollment(a):
    _order(a, AGNES, ["ash-practitioner-cert-info"])
    assert not a._is_paid_member(AGNES)


def test_the_window_closes_after_december_31(a):
    assert _REAL_WINDOW("2026-12-31")
    assert not _REAL_WINDOW("2027-01-01")


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


def test_a_refunded_biofield_does_not(a):
    oid = _order(a, AGNES, ["biofield-analysis"])
    _ledger(a, oid, "payment", 30000)
    _ledger(a, oid, "refund", 30000)
    assert not a._mix_match_member(AGNES)


def test_a_card_paid_biofield_counts_by_its_line_name(a):
    """Round 1: the card checkout stores the line by name, with no slug."""
    _order(a, AGNES, [], items=[{"name": a._BIOFIELD_ITEM_NAME, "qty": 1,
                                 "desc": a._BIOFIELD_ITEM_NAME}])
    assert a._mix_match_member(AGNES)


def test_the_remedy_match_program_is_not_a_biofield(a):
    _order(a, AGNES, [], items=[{"name": "Remedy Match Program", "qty": 1}])
    assert not a._mix_match_member(AGNES)


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


def test_a_future_dated_payment_does_not_count(a):
    later = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    _order(a, AGNES, ["biofield-analysis"], paid_at=later)
    _order(a, AGNES, ["ash-practitioner-cert-monthly"], paid_at=later)
    assert not a._mix_match_member(AGNES)


def test_a_malformed_line_does_not_hide_a_real_payment(a):
    _order(a, AGNES, [], items=[{"slug": 7}, {"name": ["x"]}])
    _order(a, AGNES, ["biofield-analysis"])
    assert a._mix_match_member(AGNES)


def test_a_legacy_paid_order_refunded_in_the_ledger_does_not(a):
    """A pre-ledger order carries paid_cents on the order row. A refund-only ledger
    row turns that fallback off, so the order reads as refunded."""
    oid = _order(a, AGNES, ["ash-practitioner-cert"])
    from dashboard import order_payments as op
    cx = sqlite3.connect(str(a.LOG_DB))
    cx.row_factory = sqlite3.Row
    op._insert(cx, oid, kind="refund", amount_cents=30000, method="zelle",
               source="manual", external_ref=None, refunds_payment_id=None,
               paid_at=None, note=None, actor=None)
    cx.close()
    assert not a._is_paid_member(AGNES)


def test_a_legacy_paid_order_with_no_ledger_counts(a):
    _order(a, AGNES, ["ash-practitioner-cert"])
    assert a._is_paid_member(AGNES)


def test_the_cache_drops_expired_entries(a, monkeypatch):
    monkeypatch.setattr(a, "_PAYMENT_CACHE",
                        {("cert", f"e{i}@x.com"): (-1e9, False) for i in range(5001)})
    a._cached_payment_check("cert", AGNES, lambda e: False)
    assert len(a._PAYMENT_CACHE) == 1


def test_a_partial_refund_on_a_card_order_keeps_it(a):
    """Round 3: a card order is paid on the order row with no ledger payment."""
    oid = _order(a, AGNES, ["biofield-analysis"])
    from dashboard import order_payments as op
    cx = sqlite3.connect(str(a.LOG_DB))
    cx.row_factory = sqlite3.Row
    op._insert(cx, oid, kind="refund", amount_cents=5000, method="card",
               source="manual", external_ref=None, refunds_payment_id=None,
               paid_at=None, note=None, actor=None)
    cx.close()
    assert a._mix_match_member(AGNES)


def test_every_client_pricing_path_uses_the_mix_match_gate():
    """Round 3: the reveal cart, the Biofield order preview, /begin checkout, reorder
    subscribe and the owner price preview read real membership only, so they
    disagreed with the invoice. The practitioner checkout stays on real membership."""
    import ast
    import inspect
    import textwrap
    import app as mod

    def calls(fn):
        fn = getattr(fn, "__wrapped__", fn)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    for name in ("_checkout_cart", "begin_biofield_order_preview", "begin_checkout",
                 "api_orders_price_preview", "_price_inhouse_invoice", "reorder_subscribe"):
        assert "_mix_match_member" in calls(getattr(mod, name)), name
    assert "_mix_match_member" not in calls(mod.api_client_checkout)
