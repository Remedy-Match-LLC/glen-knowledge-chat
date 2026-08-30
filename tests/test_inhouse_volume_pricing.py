"""In-house order-entry volume pricing.

$69.97 functional-formulation capsules get a linear volume rate; everything
else stays at list price; server is the sole price authority.

Glen policy (2026-07): the order-wide MIX/MATCH aggregation (rate driven by
the TOTAL FF quantity across every different FF SKU in the order) is a
PAID-MEMBER-ONLY perk. A non-paid / $1-trial member still keeps the SAME-SKU
quantity discount (rate driven by just that line's own qty) — they just don't
get credit for other SKUs in the cart toward the ramp. Both directions are
covered below: paid-member (order-wide) behavior is unchanged from before;
non-member behavior now depends on program_member/line_qty.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import pricing as _pricing


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # missing env in some CI
        pytest.skip(f"app not importable: {e}")


FF = {"slug": "brain", "qty_pricing": True, "price_cents": 6997, "name": "Brain Boost", "bottle_type": "30 Caps"}
FF2 = {"slug": "bone", "qty_pricing": True, "price_cents": 6997, "name": "Bone Builder", "bottle_type": "30 Caps"}
FF3 = {"slug": "calm", "qty_pricing": True, "price_cents": 6997, "name": "Calm", "bottle_type": "30 Caps"}
FF4 = {"slug": "detox", "qty_pricing": True, "price_cents": 6997, "name": "Detox", "bottle_type": "30 Caps"}
FF5 = {"slug": "focus", "qty_pricing": True, "price_cents": 6997, "name": "Focus", "bottle_type": "30 Caps"}
FF6 = {"slug": "sleep", "qty_pricing": True, "price_cents": 6997, "name": "Sleep", "bottle_type": "30 Caps"}
NONFF = {"slug": "mix", "price_cents": 7000, "name": "Drink Mix", "bottle_type": "30 Caps"}
_CAT = {"brain": FF, "bone": FF2, "calm": FF3, "detox": FF4, "focus": FF5, "sleep": FF6,
        "mix": NONFF}
_SIX_FF_SLUGS = ["brain", "bone", "calm", "detox", "focus", "sleep"]


# ── _inhouse_ff_unit_cents: raw rate math ────────────────────────────────────

def test_ff_unit_cents_member_uses_order_wide_qty():
    """Paid member: rate driven by the TOTAL FF qty across the order (today's
    behavior, unchanged), regardless of this line's own qty."""
    appmod = _app()
    s = _pricing.load_settings(None)
    f = appmod._inhouse_ff_unit_cents
    assert f(FF, 1, s, program_member=True, line_qty=1) == 6997
    assert f(FF, 3, s, program_member=True, line_qty=1) == 6628
    assert f(FF, 6, s, program_member=True, line_qty=2) == 6075
    assert f(FF, 12, s, program_member=True, line_qty=1) == 5000   # FF $50 floor
    assert f(FF, 99, s, program_member=True, line_qty=1) == 5000
    assert f(NONFF, 12, s, program_member=True) == 7000            # non-FF unaffected


def test_ff_unit_cents_nonmember_uses_line_qty_same_sku():
    """Non-member: the order-wide mix/match aggregation is OFF — rate is driven
    by THIS line's own qty only (same-SKU), even when other FF SKUs in the cart
    push the order's total_ff_qty much higher."""
    appmod = _app()
    s = _pricing.load_settings(None)
    f = appmod._inhouse_ff_unit_cents
    assert f(FF, 6, s, program_member=False, line_qty=1) == 6997   # mix/match qty1 line -> list
    assert f(FF, 6, s, program_member=False, line_qty=6) == 6075   # same-SKU qty6 == member@6
    assert f(FF, 99, s, program_member=False, line_qty=12) == 5000  # same-SKU floor
    assert f(FF, 99, s, program_member=False, line_qty=1) == 6997  # huge order, qty1 line -> list
    assert f(NONFF, 99, s, program_member=False, line_qty=1) == 7000  # non-FF unaffected


def test_ff_unit_cents_default_is_fail_safe_list_price():
    """A caller that forgets to pass program_member/line_qty gets LIST price
    (not silently discounted) — the fail-safe default."""
    appmod = _app()
    s = _pricing.load_settings(None)
    assert appmod._inhouse_ff_unit_cents(FF, 6, s) == 6997


def test_total_ff_qty_sums_only_ff(monkeypatch):
    appmod = _app()
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    lines = [{"slug": "brain", "qty": 4}, {"slug": "bone", "qty": 2}, {"slug": "mix", "qty": 5}]
    assert appmod._inhouse_total_ff_qty(lines) == 6  # 4+2; mix excluded


def test_multi_ff_lines_member_share_total_rate():
    """Paid member: total FF qty 6 -> BOTH FF lines priced at the vp(6) rate,
    even a qty-2 line (order-wide mix/match)."""
    appmod = _app()
    s = _pricing.load_settings(None)
    assert appmod._inhouse_ff_unit_cents(FF, 6, s, program_member=True, line_qty=4) == 6075
    assert appmod._inhouse_ff_unit_cents(FF2, 6, s, program_member=True, line_qty=2) == 6075


def test_multi_ff_lines_nonmember_use_own_qty_not_total():
    """Non-member: same order (total FF qty 6 across two different SKUs), but
    each line only sees its OWN qty -> no mix/match aggregation."""
    appmod = _app()
    s = _pricing.load_settings(None)
    assert appmod._inhouse_ff_unit_cents(FF, 6, s, program_member=False, line_qty=4) == 6444  # vp(4)
    assert appmod._inhouse_ff_unit_cents(FF2, 6, s, program_member=False, line_qty=2) == 6813  # vp(2)


def test_combined_family_shipment_pools_ff_volume_and_preserves_other_lines(monkeypatch):
    from dashboard import combined_shipments as combined
    from dashboard import family_plan, household, orders

    appmod = _app()
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    orders.init_orders_table(cx)
    combined.init_combined_shipments_table(cx)
    household.init_household_tables(cx)
    family_plan.init_family_plan_table(cx)
    caregiver = "parent@example.com"
    household.add_member(cx, caregiver, "child@example.com", relationship="child")
    family_plan.activate(cx, caregiver, next_charge_at=None, source="comp")

    first_items = [
        {"slug": "brain", "name": "Brain", "qty": 1,
         "unit_cents": 6997, "line_cents": 6997},
        {"slug": "bone", "name": "Courtesy Bone", "qty": 1,
         "unit_cents": 4000, "line_cents": 4000, "override": True},
        {"slug": "mix", "name": "Service", "qty": 1,
         "unit_cents": 7000, "line_cents": 7000},
    ]
    second_items = [{"slug": "calm", "name": "Calm", "qty": 2,
                     "unit_cents": 6997, "line_cents": 13994}]
    first = orders.upsert_order(
        cx, source="manual", external_ref="family-a", email=caregiver,
        name="Parent", items=first_items, total_cents=18997, shipping_cents=1000,
        address={"street": "1 Main", "city": "Tulsa", "state": "OK", "zip": "74101"})
    second = orders.upsert_order(
        cx, source="manual", external_ref="family-b", email="child@example.com",
        name="Child", items=second_items, total_cents=15294, shipping_cents=1300,
        address={"street": "1 Main", "city": "Tulsa", "state": "OK", "zip": "74101"})
    shipment = combined.create_shipment(cx, [first, second])

    monkeypatch.setattr(appmod, "_family_plan_enabled", lambda: True)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_pricing_settings", lambda: _pricing.load_settings(None))
    monkeypatch.setattr(
        appmod, "_price_cart",
        lambda cart, **kwargs: {"shipping_cents": 2300 if len(cart) > 3 else 1000})
    result = appmod._recompute_combined_shipping(cx, shipment["id"])

    assert result["pooled_pricing"]["plan_holder"] == caregiver
    assert result["pooled_pricing"]["eligible_quantity"] == 4
    first_after = orders.get_order(cx, first)
    second_after = orders.get_order(cx, second)
    by_slug = {item["slug"]: item for item in first_after["items"]}
    assert by_slug["brain"]["unit_cents"] == 6444       # household qty 4
    assert by_slug["bone"]["unit_cents"] == 4000        # explicit override frozen
    assert by_slug["mix"]["unit_cents"] == 7000         # non-FF frozen
    assert second_after["items"][0]["unit_cents"] == 6444
    assert sum(member["new_shipping_cents"] for member in result["members"]) == 2300
    cx.close()


def test_line_unit_override_wins():
    appmod = _app()
    s = _pricing.load_settings(None)
    # Explicit override always wins, regardless of membership.
    assert appmod._inhouse_line_unit_cents(FF, 5000, 12, s) == 5000
    assert appmod._inhouse_line_unit_cents(
        FF, 5000, 12, s, program_member=False, line_qty=1) == 5000
    # No override, paid member: order-wide qty(12) -> FF $50 floor.
    assert appmod._inhouse_line_unit_cents(
        FF, None, 12, s, program_member=True, line_qty=1) == 5000
    # No override, non-member, this line's own qty=1: list price (order total
    # of 12 across other SKUs doesn't help a non-member).
    assert appmod._inhouse_line_unit_cents(
        FF, None, 12, s, program_member=False, line_qty=1) == 6997
    assert appmod._inhouse_line_unit_cents(NONFF, None, 12, s) == 7000


# ── /api/orders/price-preview ────────────────────────────────────────────────

def test_price_preview_route_member(monkeypatch):
    appmod = _app()
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: True)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/price-preview",
                    json={"email": "member@x.com",
                          "lines": [{"slug": "brain", "qty": 4},
                                    {"slug": "bone", "qty": 2},
                                    {"slug": "mix", "qty": 1}]},
                    headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["total_ff_qty"] == 6
    by = {l["slug"]: l for l in j["lines"]}
    assert by["brain"]["is_ff"] and by["brain"]["effective_unit_cents"] == 6075  # order-wide vp(6)
    assert by["bone"]["effective_unit_cents"] == 6075          # qty-2 FF line, order-wide rate
    assert (not by["mix"]["is_ff"]) and by["mix"]["effective_unit_cents"] == 7000
    assert j["subtotal_cents"] == 6075 * 4 + 6075 * 2 + 7000 * 1


def test_price_preview_route_nonmember(monkeypatch):
    appmod = _app()
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: False)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/price-preview",
                    json={"email": "nonmember@x.com",
                          "lines": [{"slug": "brain", "qty": 4},
                                    {"slug": "bone", "qty": 2},
                                    {"slug": "mix", "qty": 1}]},
                    headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["total_ff_qty"] == 6   # still tallied, just not usable by a non-member
    by = {l["slug"]: l for l in j["lines"]}
    assert by["brain"]["effective_unit_cents"] == 6444          # same-SKU vp(4), not vp(6)
    assert by["bone"]["effective_unit_cents"] == 6813           # same-SKU vp(2), not vp(6)
    assert by["mix"]["effective_unit_cents"] == 7000
    assert j["subtotal_cents"] == 6444 * 4 + 6813 * 2 + 7000 * 1


def test_price_preview_owner_only():
    appmod = _app()
    client = appmod.app.test_client()
    r = client.post("/api/orders/price-preview", json={"lines": []},
                    headers={"X-Console-Key": "wrong-key"})
    assert r.status_code == 401


# ── /api/orders/manual (real charge path) ────────────────────────────────────

def _orders_db(appmod, tmp_path, name="m.db"):
    db = str(tmp_path / name)
    from dashboard import orders as O
    cx = sqlite3.connect(db)
    O.init_orders_table(cx)
    cx.close()
    return db


def _orders_db_with_people(appmod, tmp_path, name="m.db"):
    """Same as _orders_db, plus a minimal people table — needed whenever the
    posted customer has an email (find_or_create_by_email touches it)."""
    from dashboard import customers as C
    db = _orders_db(appmod, tmp_path, name)
    cx = sqlite3.connect(db)
    cx.execute("""CREATE TABLE people (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
        first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', name TEXT DEFAULT '',
        phone TEXT DEFAULT '', city TEXT DEFAULT '', state TEXT DEFAULT '',
        country TEXT DEFAULT '', source TEXT DEFAULT '', order_count INTEGER DEFAULT 0,
        last_order_date TEXT DEFAULT '', created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '')""")
    cx.commit()
    C.add_people_address_columns(cx)
    cx.close()
    return db


def test_manual_charges_ff_effective_no_double_discount(monkeypatch, tmp_path):
    """Non-member, ONE FF SKU at qty 6 (same-SKU): the volume discount is
    PRESERVED (owner policy: same-SKU discount stays for everyone)."""
    appmod = _app()
    db = _orders_db(appmod, tmp_path)
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: False)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/manual", json={
        "customer": {"name": "T", "address": {"address1": "1", "city": "Hilo",
                                              "state": "HI", "zip": "96720", "country": "US"}},
        "lines": [{"slug": "brain", "qty": 6}], "method": "Zelle"},
        headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"]
    # Same-SKU qty6 charged at the effective 6075/unit; discount PRESERVED for a
    # non-member because it's all one SKU (no mix/match involved).
    assert j["lines"][0]["unit_cents"] == 6075
    assert j["totals"]["subtotal_cents"] == 6075 * 6
    assert j["totals"]["discount_cents"] == 0


def test_manual_nonmember_six_different_skus_at_list(monkeypatch, tmp_path):
    """Non-member ordering 6 DIFFERENT FF SKUs at qty 1 each (mix/match): no
    order-wide aggregation -> every line at LIST price."""
    appmod = _app()
    db = _orders_db_with_people(appmod, tmp_path, "nonmember_mix.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: False)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/manual", json={
        "customer": {"email": "trial@x.com", "name": "Trial Client",
                     "address": {"address1": "1", "city": "Hilo", "state": "HI",
                                "zip": "96720", "country": "US"}},
        "pickup": True,
        "lines": [{"slug": s, "qty": 1} for s in _SIX_FF_SLUGS],
        "method": "Zelle"},
        headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"]
    assert len(j["lines"]) == 6
    for ln in j["lines"]:
        assert ln["unit_cents"] == 6997                # LIST — no volume discount at all
    assert j["totals"]["subtotal_cents"] == 6997 * 6
    assert j["totals"]["shipping_cents"] == 0
    assert j["totals"]["discount_cents"] == 0


def test_manual_member_six_different_skus_mixmatch(monkeypatch, tmp_path):
    """Paid member ordering the SAME 6 different FF SKUs at qty 1 each: the
    order-wide mix/match rate (vp(6)) applies to every line — unchanged from
    today's behavior."""
    appmod = _app()
    db = _orders_db_with_people(appmod, tmp_path, "member_mix.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: True)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/manual", json={
        "customer": {"email": "member@x.com", "name": "Member Client",
                     "address": {"address1": "1", "city": "Hilo", "state": "HI",
                                "zip": "96720", "country": "US"}},
        "pickup": True,
        "lines": [{"slug": s, "qty": 1} for s in _SIX_FF_SLUGS],
        "method": "Zelle"},
        headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"]
    assert len(j["lines"]) == 6
    for ln in j["lines"]:
        assert ln["unit_cents"] == 6075                # order-wide vp(6)
    assert j["totals"]["subtotal_cents"] == 6075 * 6
    assert j["totals"]["discount_cents"] == 0


def test_manual_nonmember_per_line_override_still_wins(monkeypatch, tmp_path):
    """An owner-typed per-line override is respected for a non-member even
    though the automatic mix/match rate is gated off."""
    appmod = _app()
    db = _orders_db_with_people(appmod, tmp_path, "nonmember_override.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda email: False)
    client = appmod.app.test_client()
    key = appmod.dashboard.CONSOLE_SECRET or ""
    r = client.post("/api/orders/manual", json={
        "customer": {"email": "trial2@x.com", "name": "Trial Client 2",
                     "address": {"address1": "1", "city": "Hilo", "state": "HI",
                                "zip": "96720", "country": "US"}},
        "pickup": True,
        "lines": [{"slug": "brain", "qty": 1, "unit_cents": 4000},
                  {"slug": "bone", "qty": 1}],
        "method": "Zelle"},
        headers={"X-Console-Key": key})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"]
    by = {l["slug"]: l for l in j["lines"]}
    assert by["brain"]["unit_cents"] == 4000            # owner override wins, non-member or not
    assert by["brain"].get("override") is True
    assert by["bone"]["unit_cents"] == 6997              # no override, non-member qty1 -> list
    assert j["totals"]["subtotal_cents"] == 4000 + 6997
