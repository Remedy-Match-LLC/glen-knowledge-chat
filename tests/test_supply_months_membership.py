"""Rule C: one month of membership for each month of remedies ordered.

Glen, 2026-10-23: "supply length of 1, 2, or 3 months would be a selection where we
click 'Add remedies to invoice'. Extend membership say 37 days from invoice payment
and adjust to 30 days from package receipt when we have that date. Match membership
with the coaching window. Courtesy control: yes." And: "bottle count multiplies by
the number of months". Amended the same day: "Refills only extend the free
membership benefit for up to 3 months per biofield."
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import app as appmod
from biofield_local_app import create_app
from dashboard import biofield_invoice as bi
from dashboard import coaching
from dashboard import orders as O
from dashboard import tracking as T
from dashboard.biofield_authoring import init_auth_tables, create_test, add_chain_row
from dashboard.biofield_report_html import render_fee_panel


# ── The selector and the bottle multiplier (the Intake app) ──────────────────

def _fee_state():
    return {"email": "j@x.com", "has_email": True, "available": True,
            "courtesy_cents": None, "note": "", "standard_cents": 30000,
            "value_cents": 99700}


def test_the_panel_offers_one_two_or_three_months_defaulting_to_one():
    html = render_fee_panel(_fee_state())
    assert "id=supplymonths" in html
    assert "<option value=1 selected>1 month</option>" in html
    assert "<option value=2>2 months</option>" in html
    assert "<option value=3>3 months</option>" in html
    assert "supply_months:supplyMonths()" in html


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


CATALOG = [{"slug": "liver-support", "name": "Liver Support"},
           {"slug": "neuroprotect", "name": "Neuroprotect"},
           {"slug": "consult-call", "name": "Consult Call", "service": True}]


@pytest.fixture
def intake(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "default_edit_order_link", lambda oid: f"https://x/edit/{oid}")
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Donna Banks", email="d@x.com", date="2026-07-06")
        add_chain_row(cx, tid, layer=1, head="", most_affected="", remedy="Liver Support")
        add_chain_row(cx, tid, layer=2, head="", most_affected="", remedy="Neuroprotect")
        add_chain_row(cx, tid, layer=3, head="", most_affected="", remedy="Consult Call")
        cx.execute("UPDATE biofield_auth_chain SET bottles=2 WHERE remedy='Neuroprotect'")
    calls = []

    def fake_create(customer, lines, replace_open=False, invoice_note=None,
                    update_order_id=None, idempotency_key="", supply_months=None):
        calls.append({"lines": [dict(l) for l in lines], "supply_months": supply_months,
                      "update_order_id": update_order_id})
        return {"ok": True, "order_id": 7, "external_ref": "INH-Z", "total_cents": 1,
                "error": None, "accepted_slugs": [l["slug"] for l in lines]}

    # The open invoice the second raise refreshes: Liver Support already at 3 (the
    # first raise's 1 x 3), plus a line Rae added by hand in Edit Invoice.
    open_invoice = {"ok": True, "order_id": 7, "status": "proposed", "pay_status": "unpaid",
                    "items": [{"slug": "liver-support", "qty": 3, "source": "biofield"},
                              {"slug": "neuroprotect", "qty": 6, "source": "biofield"},
                              {"slug": "hand-added", "qty": 2, "source": "self"}]}
    app = create_app(db_path=db, invoice_fetch_catalog=lambda: CATALOG,
                     invoice_create=fake_create,
                     invoice_link=lambda oid: {"ok": False},
                     invoice_latest=lambda email: open_invoice)
    app.testing = True
    c = app.test_client()
    c._calls, c._tid = calls, tid
    return c


def _qty(lines, slug):
    return next(l["qty"] for l in lines if l["slug"] == slug)


def test_the_selector_value_reaches_the_order_call(intake):
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": 3})
    assert intake._calls[-1]["supply_months"] == 3


def test_anything_but_one_two_or_three_is_one(intake):
    for bad in (4, 0, -2, "x", None, 2.5):
        intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": bad})
        assert intake._calls[-1]["supply_months"] == 1
        assert _qty(intake._calls[-1]["lines"], "liver-support") == 1


@pytest.mark.parametrize("months", [1, 2, 3])
def test_bottles_multiply_by_the_months(intake, months):
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": months})
    lines = intake._calls[-1]["lines"]
    assert _qty(lines, "liver-support") == 1 * months
    assert _qty(lines, "neuroprotect") == 2 * months     # its own bottles x N


def test_services_never_multiply(intake):
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": 3})
    lines = intake._calls[-1]["lines"]
    assert _qty(lines, "biofield-analysis") == 1
    assert _qty(lines, "consult-call") == 1


def test_a_repeated_raise_never_compounds(intake):
    """The open invoice already carries 3 and 6. A second raise at 3 months is still
    base x 3, never 3 x 3."""
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": 3})
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": 3})
    lines = intake._calls[-1]["lines"]
    assert intake._calls[-1]["update_order_id"] == 7
    assert _qty(lines, "liver-support") == 3
    assert _qty(lines, "neuroprotect") == 6


def test_a_hand_added_line_is_kept_as_entered_and_not_multiplied(intake):
    intake.post(f"/author/{intake._tid}/invoice", json={"supply_months": 3})
    assert _qty(intake._calls[-1]["lines"], "hand-added") == 2


def test_build_invoice_lines_multiplies_only_remedies():
    built = bi.build_invoice_lines({}, [{"name": "Liver Support", "qty": 2}], CATALOG,
                                   include_fee=True, supply_months=2)
    assert built["lines"][0] == {"slug": "biofield-analysis", "qty": 1}
    assert _qty(built["lines"], "liver-support") == 4


# ── Payment, delivery, cap (the production app) ──────────────────────────────

EMAIL = "client@example.com"
REMEDY = [{"slug": "liver-support", "qty": 1, "unit_cents": 6997, "line_cents": 6997}]
BIOFIELD = [{"slug": "biofield-analysis", "qty": 1, "unit_cents": 30000, "line_cents": 30000}]


@pytest.fixture
def cx(tmp_path, monkeypatch):
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_member_join_welcome", lambda *a, **k: None)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    appmod.init_membership_tables(c)
    coaching.init_coaching_table(c)
    T.init_tracking_schema(c)
    T.migrate_add_delivery_columns(c)
    appmod._init_people_table()          # opens LOG_DB itself
    yield c
    c.close()


def _ago(days):
    return datetime.utcnow() - timedelta(days=days)


def _iso(dt):
    return dt.isoformat() + "Z"


def _order(cx, ref, items, *, source="in-house", months=None, paid_days_ago=0,
           email=EMAIL, status="new"):
    oid = O.upsert_order(cx, source=source, external_ref=ref, email=email,
                         items=items, total_cents=1000, status=status)
    cx.execute("UPDATE orders SET pay_status='paid', paid_at=? WHERE id=?",
               (_iso(_ago(paid_days_ago)), oid))
    if months is not None:
        O.set_order_supply_months(cx, oid, months)
    cx.commit()
    return O.get_order(cx, oid)


def _biofield(cx, days_ago=0, ref="BF-1"):
    return _order(cx, ref, BIOFIELD, paid_days_ago=days_ago, status="done")


def _grant_row(cx, ref):
    return cx.execute("SELECT * FROM order_supply_grants WHERE order_ref=?", (ref,)).fetchone()


def _expiry(cx, membership_id):
    return datetime.fromisoformat(cx.execute(
        "SELECT expires_at FROM memberships WHERE id=?",
        (membership_id,)).fetchone()[0].rstrip("Z"))


def _days_from_now(dt):
    return (dt - datetime.utcnow()).total_seconds() / 86400


@pytest.mark.parametrize("months,days", [(1, 37), (2, 67), (3, 90)])
def test_the_paid_grant_runs_37_days_plus_30_per_extra_month(cx, months, days):
    _biofield(cx)
    o = _order(cx, "INH-A", REMEDY, months=months)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "granted"
    g = _grant_row(cx, "INH-A")
    assert g["months"] == months
    assert abs(_days_from_now(_expiry(cx, g["membership_id"])) - days) < 0.01
    w = coaching.window_for_order(cx, o["id"])
    assert w["ends_at"] == g["ends_at"] and w["started_at"] == g["starts_at"]


def test_an_order_without_supply_months_counts_as_one(cx):
    _biofield(cx)
    o = _order(cx, "INH-OLD", REMEDY)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "granted"
    assert _grant_row(cx, "INH-OLD")["months"] == 1


def test_a_second_supply_stacks_after_the_first(cx):
    _biofield(cx)
    first = _order(cx, "INH-1", REMEDY, months=1)
    second = _order(cx, "INH-2", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, first)
    end1 = _grant_row(cx, "INH-1")["ends_at"]
    assert appmod._grant_supply_membership_on_paid(cx, second) == "granted"
    g2 = _grant_row(cx, "INH-2")
    assert g2["prior_end"] == end1 and g2["starts_at"] == end1
    # 37 + 37 = 74 days, still inside the 90-day cap
    assert abs(_days_from_now(_expiry(cx, g2["membership_id"])) - 74) < 0.01


def test_the_care_taster_overlaps_and_never_stacks(cx):
    bf = _biofield(cx)
    assert appmod._grant_biofield_line_on_paid(cx, bf) == "granted"
    o = _order(cx, "INH-CT", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, o)
    g = _grant_row(cx, "INH-CT")
    assert g["prior_end"] is None
    assert abs(_days_from_now(_expiry(cx, g["membership_id"])) - 37) < 0.01


def test_a_paid_member_is_skipped(cx):
    _biofield(cx)
    appmod._grant_membership(cx, EMAIL, 365, appmod._mp.get_tier("month")["source"])
    cx.commit()
    o = _order(cx, "INH-M", REMEDY, months=3)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "member"
    assert _grant_row(cx, "INH-M") is None


def test_the_grant_is_idempotent(cx):
    _biofield(cx)
    o = _order(cx, "INH-I", REMEDY, months=2)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "granted"
    assert appmod._grant_supply_membership_on_paid(cx, o) == "already"
    n = cx.execute("SELECT COUNT(*) FROM memberships WHERE source=?",
                   (appmod.SUPPLY_SOURCE,)).fetchone()[0]
    assert n == 1


def test_a_service_only_or_non_qualifying_order_earns_nothing(cx):
    _biofield(cx)
    svc = _order(cx, "INH-S", BIOFIELD, months=3)
    assert appmod._grant_supply_membership_on_paid(cx, svc) == "none"
    whole = _order(cx, "W-1", REMEDY, source="wholesale", months=3)
    assert appmod._grant_supply_membership_on_paid(cx, whole) == "none"


def test_the_paid_hook_reaches_the_grant_through_the_settlement_dep(cx):
    _biofield(cx)
    o = _order(cx, "INH-DEP", REMEDY, months=2)
    appmod._grant_membership_line_dep(o)
    assert _grant_row(cx, "INH-DEP") is not None


# ── The cap: 3 months per Biofield ───────────────────────────────────────────

def test_the_cap_limits_the_paid_grant(cx):
    _biofield(cx, days_ago=70)            # cap = 20 days from now
    o = _order(cx, "INH-CAP", REMEDY, months=3)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "granted"
    g = _grant_row(cx, "INH-CAP")
    assert abs(_days_from_now(_expiry(cx, g["membership_id"])) - 20) < 0.01


def test_a_refill_after_the_cap_writes_nothing_and_stays_idempotent(cx):
    _biofield(cx, days_ago=100)
    o = _order(cx, "INH-LATE", REMEDY, months=1)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "cap_passed"
    g = _grant_row(cx, "INH-LATE")
    assert g["reason"] == "cap_passed" and g["membership_id"] is None
    assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0
    assert appmod._grant_supply_membership_on_paid(cx, o) == "already"


def test_no_biofield_means_no_supply_grant(cx):
    o = _order(cx, "INH-NB", REMEDY, months=3)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "no_biofield"
    assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0


def test_a_new_biofield_reopens_the_cap(cx):
    _biofield(cx, days_ago=200, ref="BF-OLD")
    late = _order(cx, "INH-R1", REMEDY, months=1)
    assert appmod._grant_supply_membership_on_paid(cx, late) == "cap_passed"
    _biofield(cx, days_ago=0, ref="BF-NEW")
    fresh = _order(cx, "INH-R2", REMEDY, months=1)
    assert appmod._grant_supply_membership_on_paid(cx, fresh) == "granted"


def test_an_unpaid_open_biofield_invoice_does_not_anchor_the_cap(cx):
    oid = O.upsert_order(cx, source="in-house", external_ref="BF-OPEN", email=EMAIL,
                         items=BIOFIELD, total_cents=30000, status="proposed")
    cx.commit()
    assert appmod._supply_cap_end(cx, EMAIL) is None
    assert oid


# ── Delivery ────────────────────────────────────────────────────────────────

def _ship(cx, order, tn):
    sid = T.record_shipment(cx, tracking_number=tn, order_uuid=order["external_ref"],
                            status="sent")
    cx.commit()
    return T.shipment_by_tracking(cx, tn), sid


def test_delivery_resets_membership_and_coaching_to_30n_from_receipt(cx):
    _biofield(cx, days_ago=5)
    o = _order(cx, "INH-D", REMEDY, months=2, paid_days_ago=5)
    appmod._grant_supply_membership_on_paid(cx, o)
    delivered = _ago(1)
    sh, _ = _ship(cx, o, "TN-D")
    res = appmod._activate_coaching_for_shipment(cx, sh, delivered_at=_iso(delivered))
    assert res["ok"] is True
    g = _grant_row(cx, "INH-D")
    end = delivered + timedelta(days=60)
    assert g["delivered_at"] == _iso(delivered)
    assert _expiry(cx, g["membership_id"]) == end
    w = coaching.window_for_order(cx, o["id"])
    assert w["started_at"] == _iso(delivered) and w["ends_at"] == _iso(end)


def test_delivery_before_prior_end_starts_at_prior_end(cx):
    _biofield(cx)
    first = _order(cx, "INH-P1", REMEDY, months=1)
    second = _order(cx, "INH-P2", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, first)
    appmod._grant_supply_membership_on_paid(cx, second)
    prior = datetime.fromisoformat(_grant_row(cx, "INH-P2")["prior_end"].rstrip("Z"))
    res = appmod._adjust_supply_on_delivery(cx, second["id"], _iso(_ago(0)))
    assert res["granted"] is True
    g = _grant_row(cx, "INH-P2")
    assert g["starts_at"] == _iso(prior)
    assert _expiry(cx, g["membership_id"]) == min(prior + timedelta(days=30),
                                                  appmod._supply_cap_end(cx, EMAIL))


def test_the_cap_limits_the_delivery_adjustment(cx):
    _biofield(cx, days_ago=80)            # cap = 10 days from now
    o = _order(cx, "INH-DC", REMEDY, months=3, paid_days_ago=1)
    appmod._grant_supply_membership_on_paid(cx, o)
    appmod._adjust_supply_on_delivery(cx, o["id"], _iso(_ago(0)))
    g = _grant_row(cx, "INH-DC")
    assert abs(_days_from_now(_expiry(cx, g["membership_id"])) - 10) < 0.01


def test_the_biofield_month_is_not_added_for_a_supply_delivery(cx, monkeypatch):
    _biofield(cx, days_ago=3)
    o = _order(cx, "INH-BM", REMEDY, months=1, paid_days_ago=3)
    appmod._grant_supply_membership_on_paid(cx, o)
    called = []
    monkeypatch.setattr(appmod, "_extend_biofield_month_on_delivery",
                        lambda *a, **k: called.append(a) or "granted")
    sh, _ = _ship(cx, o, "TN-BM")
    appmod._activate_coaching_for_shipment(cx, sh, delivered_at=_iso(_ago(0)))
    assert called == []


def test_an_order_without_a_claim_still_gets_the_biofield_month(cx, monkeypatch):
    o = _order(cx, "INH-NC", REMEDY, months=1)
    called = []
    monkeypatch.setattr(appmod, "_extend_biofield_month_on_delivery",
                        lambda *a, **k: called.append(a) or "none")
    sh, _ = _ship(cx, o, "TN-NC")
    appmod._activate_coaching_for_shipment(cx, sh, delivered_at=_iso(_ago(0)))
    assert len(called) == 1


def test_a_supply_order_opens_coaching_without_the_eligibility_gate(cx):
    """An in-house order is not a coaching source, and the client held no membership
    when it was created. The supply claim opens the window anyway."""
    _biofield(cx)
    o = _order(cx, "INH-G", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, o)
    cx.execute("DELETE FROM coaching_windows")
    cx.commit()
    sh, _ = _ship(cx, o, "TN-G")
    res = appmod._activate_coaching_for_shipment(cx, sh, delivered_at=_iso(_ago(0)))
    assert res["ok"] is True
    assert coaching.window_for_order(cx, o["id"]) is not None


def test_a_delivery_adjustment_never_raises(cx, monkeypatch):
    _biofield(cx)
    o = _order(cx, "INH-E", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, o)
    monkeypatch.setattr(appmod, "_supply_cap_end",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert appmod._adjust_supply_on_delivery(cx, o["id"], _iso(_ago(0)))["granted"] is False


# ── Courtesy ────────────────────────────────────────────────────────────────

@pytest.fixture
def console(cx, monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "sekret", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "sekret", raising=False)
    return appmod.app.test_client()


def _courtesy(client, body, key="sekret"):
    return client.post(f"/api/console/membership/courtesy?key={key}", json=body)


def test_courtesy_needs_the_console_key(console):
    r = _courtesy(console, {"email": EMAIL, "days": 10, "note": "why"}, key="wrong")
    assert r.status_code == 401


def test_courtesy_needs_a_note(console, cx):
    r = _courtesy(console, {"email": EMAIL, "days": 10, "note": "  "})
    assert r.status_code == 400
    assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0


def test_courtesy_adds_days_after_the_furthest_expiry_with_an_audit_row(console, cx):
    _biofield(cx)
    o = _order(cx, "INH-CO", REMEDY, months=1)
    appmod._grant_supply_membership_on_paid(cx, o)
    supply_end = datetime.fromisoformat(_grant_row(cx, "INH-CO")["ends_at"].rstrip("Z"))
    r = _courtesy(console, {"email": EMAIL, "days": 14, "note": "parcel lost in the mail"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["expires_at"] == _iso(supply_end + timedelta(days=14))
    log = cx.execute("SELECT * FROM membership_courtesy_log").fetchone()
    assert log["note"] == "parcel lost in the mail" and log["days"] == 14
    assert log["actor"] and log["membership_id"] == j["membership_id"]
    assert cx.execute("SELECT source FROM memberships WHERE id=?",
                      (j["membership_id"],)).fetchone()[0] == "courtesy"


def test_courtesy_is_never_capped(console, cx):
    _biofield(cx, days_ago=89)            # the supply cap is 1 day away
    r = _courtesy(console, {"email": EMAIL, "days": 120, "note": "warranted"})
    exp = datetime.fromisoformat(r.get_json()["expires_at"].rstrip("Z"))
    assert _days_from_now(exp) > 119


# ── The order records supply_months ──────────────────────────────────────────

@pytest.fixture
def owner(cx, monkeypatch):
    from dashboard import rbac
    monkeypatch.setattr(appmod, "_bos_actor", lambda: rbac.Actor(role="owner", name="glen"))
    monkeypatch.setattr(appmod, "_push_invoice_edit_to_qbo", lambda *a, **k: {"pushed": False})
    monkeypatch.setattr(appmod, "_hold_new_order_and_invite", lambda *a, **k: None, raising=False)
    return appmod.app.test_client()


def _manual(client, **extra):
    return client.post("/api/orders/manual", json={
        "customer": {"email": EMAIL, "name": "C"}, "pickup": True,
        "lines": [{"slug": "clear-lens-eye-drops", "qty": 1}], **extra})


@pytest.mark.parametrize("sent,stored", [(2, 2), (3, 3), (9, 1), ("x", 1)])
def test_the_manual_order_records_supply_months(owner, cx, sent, stored):
    j = _manual(owner, supply_months=sent).get_json()
    assert j["ok"], j
    assert O.get_order(cx, j["order_id"])["supply_months"] == stored


def test_an_order_raised_without_it_stores_nothing_and_reads_as_one(owner, cx):
    j = _manual(owner).get_json()
    o = O.get_order(cx, j["order_id"])
    assert o["supply_months"] is None
    assert appmod._order_supply_months(o) == 1


def test_a_refresh_of_the_open_order_updates_supply_months(owner, cx):
    j = _manual(owner, supply_months=3).get_json()
    j2 = _manual(owner, supply_months=1, update_order_id=j["order_id"]).get_json()
    assert j2["ok"] and j2.get("updated"), j2
    assert O.get_order(cx, j["order_id"])["supply_months"] == 1


def test_the_settlement_hub_dispatches_a_remedy_order_from_a_qualifying_source():
    from dashboard import order_settlement as osx
    called = []

    class _D:
        def __getattr__(self, name):
            return lambda *a, **k: called.append(name)

    for src, expect in (("in-house", True), ("reorder", True), ("wholesale", False)):
        called.clear()
        osx.settle_paid_order_effects(
            kind="in-house", order={"id": 1, "email": EMAIL, "source": src, "items": REMEDY},
            md={"invoice_id": "x"}, pi_id="pi", sid="s", deps=_D())
        assert ("grant_membership_line" in called) is expect, src


def test_a_prepaid_term_member_is_skipped(cx):
    _biofield(cx)
    appmod._grant_membership(cx, EMAIL, 300, "prepay_year_prepay")
    cx.commit()
    o = _order(cx, "INH-P", REMEDY, months=2)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "member"
    assert _grant_row(cx, "INH-P") is None


def test_a_caregiver_billed_order_grants_nobody(cx):
    _biofield(cx)
    items = [dict(it, billed_for="the.client@example.com") for it in REMEDY]
    o = _order(cx, "INH-C", items, months=2)
    assert appmod._grant_supply_membership_on_paid(cx, o) == "none"
    assert cx.execute("SELECT COUNT(*) FROM memberships WHERE source=?",
                      (appmod.SUPPLY_SOURCE,)).fetchone()[0] == 0
