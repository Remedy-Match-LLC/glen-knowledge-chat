"""A $0 in-house order is paid as it is created (Glen, 2026-11-16).

"automatically mark $0 invoices paid." Shelf stock only comes off a PAID order, so a
free bottle raised through /api/orders/manual sat "proposed / unpaid" for ever and
never left the count. Asked whether a $0 order earns membership months like a paid
one, Glen said "no", with no exception for points or shipping credit.

So the auto-pay keeps balances honest (points redeemed and shipping credit applied
are debited, the portal receipt is made, the stock count drops) and grants nothing:
no membership, supply or Biofield-month time, no referral or scan reward, no reorder
loyalty price, no points earned, and no email. Only an order the request itself
inserted, at a genuine $0, is paid. An order already in the database never is.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import app as appmod                      # an import failure fails, never skips
from dashboard import orders as O
from dashboard import points as P
from dashboard import scan_reward as SR
from dashboard import stock_counts as SC
from dashboard import tracking as T

BOTTLE = {"slug": "mix", "price_cents": 7000, "name": "Drink Mix", "bottle_type": "120 g"}
BIOFIELD = {"slug": "biofield-analysis", "price_cents": 30000, "name": "Biofield Analysis",
            "service": True}
_CAT = {"mix": BOTTLE, "biofield-analysis": BIOFIELD}
EMAIL = "staff@x.com"
ADDR = {"address1": "1 Main St", "city": "Hilo", "state": "HI", "zip": "96720",
        "country": "US"}


def _iso(days):
    return (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = str(tmp_path / "z.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    appmod._init_people_table()
    from dashboard import db as D
    from dashboard import inbox as _inbox
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        if not D.column_exists(cx, "orders", "portal_published"):
            cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER DEFAULT 0")
        P.init_points_table(cx)
        cx.execute("CREATE TABLE IF NOT EXISTS memberships (id TEXT PRIMARY KEY, email TEXT, "
                   "granted_at TEXT, expires_at TEXT, granted_by TEXT, source TEXT, "
                   "truly_vip_ref TEXT, notes TEXT, last_reminder_at TEXT)")
        appmod._supply_init_table(cx)
        cx.commit()
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: type("A", (), {"role": appmod._bos_rbac.OWNER})())
    monkeypatch.setattr(appmod._shipping, "_default_db_path", lambda: db)
    with sqlite3.connect(db) as scx:
        appmod._shipping.init_shipping_schema(scx)

    # Stock counts on, against this database and catalog.
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    monkeypatch.delenv("STOCK_ALERT_EMAILS", raising=False)
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: db)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: _CAT)
    SC._invalidate()
    with sqlite3.connect(db) as scx:
        SC.init_tables(scx)
        SC.set_count(scx, "mix", 10, who="t", reason="shelf count")

    # Call-through spies on the app functions every grant and reward path ends in. The
    # registered hooks look these names up at call time, so any route that reaches a
    # grant, from a hook or directly, is recorded here and still does its real work.
    calls = {"grant": [], "referral": [], "points": [], "email": []}

    def _spy(name, key):
        real = getattr(appmod, name)

        def wrapper(*a, **k):
            calls[key].append(name)
            return real(*a, **k)
        monkeypatch.setattr(appmod, name, wrapper)

    _spy("_grant_membership_line_dep", "grant")
    _spy("_grant_supply_membership_on_paid", "grant")
    _spy("_settle_referral", "referral")
    _spy("_settle_referrer_reward", "referral")
    _spy("_settle_order_points", "points")
    monkeypatch.setattr(_inbox, "send_email", lambda *a, **k: calls["email"].append(a))
    monkeypatch.setattr(SC, "_send_one", lambda *a, **k: calls["email"].append(a) or True)
    yield db, calls
    SC._invalidate()


def _post(*, qty=1, extra=None, email=EMAIL, pickup=True, lines=None):
    body = {"customer": {"name": "Staff", "email": email},
            "lines": lines or [{"slug": "mix", "qty": qty}], "pickup": pickup}
    if not pickup:
        body["customer"]["address"] = ADDR
    body.update(extra or {})
    r = appmod.app.test_client().post("/api/orders/manual", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _row(db, oid):
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        return dict(cx.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())


def _count(db):
    with sqlite3.connect(db) as cx:
        return SC.get_count(cx, "mix")


def _insert(db, ref, *, items=None, total=0, status="proposed", email=EMAIL, **kw):
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        oid = O.upsert_order(cx, source="in-house", external_ref=ref, status=status,
                             email=email, name="Staff",
                             items=items if items is not None else
                             [{"slug": "mix", "qty": 1, "unit_cents": 7000,
                               "line_cents": 7000}],
                             total_cents=total, **kw)
        cx.commit()
    return oid


def _unpaid(o):
    return (o.get("pay_status") or "unpaid") != "paid"


# ── The core behaviour ───────────────────────────────────────────────────────

def test_a_zero_dollar_order_is_paid_with_the_zero_total_method(env):
    db, calls = env
    res = _post(extra={"discount_cents": 7000})
    assert res["totals"]["total_cents"] == 0
    assert res["auto_paid"] is True
    o = _row(db, res["order_id"])
    assert o["pay_status"] == "paid"
    assert o["pay_method"] == "zero-total"
    assert o["paid_cents"] == 0
    assert o["status"] == "new"            # the Paid lane of the fulfillment board
    assert o["paid_at"]
    assert o["portal_published"] == 1 and o["invoice_token"]   # the portal receipt


def test_one_cent_stays_unpaid(env):
    db, calls = env
    res = _post(extra={"discount_cents": 6999})
    assert res["totals"]["total_cents"] == 1
    assert res["auto_paid"] is False
    o = _row(db, res["order_id"])
    assert o["status"] == "proposed" and _unpaid(o)
    assert _count(db) == 10


def test_the_stock_count_drops_by_the_line_qty(env):
    db, calls = env
    _post(qty=3, extra={"discount_cents": 21000})
    assert _count(db) == 7


def test_a_shipped_zero_order_is_paid_too(env):
    """Not pickup: the shipping is a typed $0 (a comped shipment), so the $0 is real."""
    db, calls = env
    res = _post(pickup=False, extra={"discount_cents": 7000, "shipping_cents": 0,
                                     "shipping_typed": True})
    assert res["totals"]["total_cents"] == 0
    assert res["auto_paid"] is True
    o = _row(db, res["order_id"])
    assert o["channel"] == "retail" and o["status"] == "new" and o["pay_status"] == "paid"
    assert _count(db) == 9


def test_no_grant_no_reward_no_points_earned_no_email(env):
    """The client has a paid Biofield, so a PAID remedy order would earn supply months
    (rule C). The control at the end proves this setup grants when the grant runs."""
    db, calls = env
    _insert(db, "INH-BF-PAID", items=[{"slug": "biofield-analysis", "qty": 1,
                                       "unit_cents": 30000, "line_cents": 30000}],
            total=30000)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid', paid_at=?, status='done' "
                   "WHERE external_ref='INH-BF-PAID'", (_iso(-5),))
        cx.commit()
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    assert calls == {"grant": [], "referral": [], "points": [], "email": []}
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM order_supply_grants").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM points_ledger WHERE reason IN "
                          "('earn','fee_free')").fetchone()[0] == 0
    # Control: the grant itself, run on this order, would have granted.
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        order = O.get_order(cx, res["order_id"])
        assert appmod._grant_supply_membership_on_paid(cx, order) == "granted"


def test_points_redeemed_are_debited(env):
    db, calls = env
    with sqlite3.connect(db) as cx:
        P.credit(cx, EMAIL, value_cents=3900, reason="seed", order_ref="SEED")
        cx.commit()
    # Points stop at the product's floor ($39 of $70 here), so a discount does the rest.
    res = _post(extra={"discount_cents": 3100, "points_redeem_cents": 3900})
    assert res["totals"]["points_redeemed_cents"] == 3900
    assert res["totals"]["total_cents"] == 0
    assert res["auto_paid"] is True
    ref = res["external_ref"]
    with sqlite3.connect(db) as cx:
        assert P.balance(cx, EMAIL) == 0
        assert P.has_entry(cx, order_ref=ref, reason="redeem")
        assert not P.has_entry(cx, order_ref=ref, reason="earn")


def test_shipping_credit_applied_is_debited(env, monkeypatch):
    db, calls = env
    monkeypatch.setenv("SHIP_CREDIT_AUTOAPPLY_ENABLED", "1")
    from dashboard import ship_credit as SHC
    with sqlite3.connect(db) as cx:
        SHC.grant(cx, EMAIL, 7000, source_ref="OLD-1")
        cx.commit()
    res = _post()
    o = _row(db, res["order_id"])
    assert o["ship_credit_applied_cents"] == 7000 and o["total_cents"] == 0
    assert res["auto_paid"] is True
    with sqlite3.connect(db) as cx:
        assert SHC.balance(cx, EMAIL) == 0


def test_an_existing_unpaid_zero_order_is_not_touched(env):
    """Like prod #310: a $0 order already in the database stays unpaid."""
    db, calls = env
    old = _insert(db, "INH-OLD310", items=[{"slug": "mix", "qty": 1, "unit_cents": 0,
                                            "line_cents": 0}])
    before = _row(db, old)
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    assert _row(db, old) == before
    assert _count(db) == 9                  # only the new order's bottle


# ── Review finding 1: only an order THIS request inserted ────────────────────

def test_an_existing_row_the_upsert_updates_is_never_auto_paid(env, monkeypatch):
    """upsert_order UPDATEs a row that already holds the reference. Force the random
    reference to collide with an existing unpaid $0 order."""
    db, calls = env
    old = _insert(db, "INH-AAAAAAAAAA", items=[{"slug": "mix", "qty": 1,
                                                "unit_cents": 0, "line_cents": 0}])
    fake = type("U", (), {"hex": "a" * 32})()
    monkeypatch.setattr(appmod.uuid, "uuid4", lambda: fake)
    res = _post(extra={"discount_cents": 7000})
    assert res["order_id"] == old           # the upsert updated the existing row
    assert res["auto_paid"] is False
    o = _row(db, old)
    assert o["status"] == "proposed" and _unpaid(o)
    assert _count(db) == 10


def test_an_idempotent_repeat_reports_auto_paid(env):
    db, calls = env
    first = _post(extra={"discount_cents": 7000, "idempotency_key": "load-1"})
    again = _post(extra={"discount_cents": 7000, "idempotency_key": "load-1"})
    assert first["auto_paid"] is True
    assert again["idempotent_repeat"] is True and again["auto_paid"] is True
    assert again["order_id"] == first["order_id"]
    assert _count(db) == 9


# ── Review finding 2: points first ────────────────────────────────────────────

def test_points_that_cannot_be_debited_leave_the_order_proposed(env):
    """The balance was spent elsewhere after the order was priced. No paid order may
    carry an unfunded points discount."""
    db, calls = env
    oid = _insert(db, "INH-PTS", total=0, points_redeemed_cents=3900, discount_cents=3100)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert O.autopay_zero_total_order(cx, oid) is False
    o = _row(db, oid)
    assert o["status"] == "proposed" and _unpaid(o)
    assert not o["invoice_token"]
    assert _count(db) == 10
    with sqlite3.connect(db) as cx:
        assert not P.has_entry(cx, order_ref="INH-PTS", reason="redeem")


# ── Review finding 3: the conditional paid UPDATE ─────────────────────────────

def test_the_paid_update_only_moves_a_proposed_unpaid_order(env):
    db, calls = env
    oid = _insert(db, "INH-CONF", status="confirmed")
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert O._mark_proposed_order_paid(cx, oid, method="zero-total",
                                           amount_cents=0) is False
    o = _row(db, oid)
    assert o["status"] == "confirmed" and _unpaid(o) and not o["invoice_token"]
    assert _count(db) == 10


def test_a_second_auto_pay_changes_nothing(env):
    db, calls = env
    oid = _insert(db, "INH-TWICE", discount_cents=7000)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert O.autopay_zero_total_order(cx, oid) is True
        paid_at = O.get_order(cx, oid)["paid_at"]
        # Straight at the UPDATE, past the helper's own pre-checks.
        assert O._mark_proposed_order_paid(cx, oid, method="zero-total",
                                           amount_cents=0) is False
        assert O.get_order(cx, oid)["paid_at"] == paid_at
    assert _count(db) == 9


# ── Review finding 4: no Biofield month on delivery ──────────────────────────

def _deliver(db, oid):
    """Ship and deliver one order through the real delivery path."""
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        from dashboard import coaching
        coaching.init_coaching_table(cx)
        T.init_tracking_schema(cx)
        T.migrate_add_delivery_columns(cx)
        ref = O.get_order(cx, oid)["external_ref"]
        tn = f"TN-{oid}"
        T.record_shipment(cx, tracking_number=tn, order_uuid=ref, status="sent")
        cx.commit()
        appmod._activate_coaching_for_shipment(cx, T.shipment_by_tracking(cx, tn),
                                               delivered_at=_iso(0))
        cx.commit()


def _biofield_months(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM memberships WHERE source=?",
                          (appmod.BIOFIELD_MONTH_SOURCE,)).fetchone()[0]


def test_a_delivered_zero_order_starts_no_biofield_month(env, monkeypatch):
    db, calls = env
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: None)
    _insert(db, "INH-BF-OLD", items=[{"slug": "biofield-analysis", "qty": 1,
                                      "unit_cents": 30000, "line_cents": 30000}],
            total=30000)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid', status='done', created_at=? "
                   "WHERE external_ref='INH-BF-OLD'", (_iso(-20),))
        cx.commit()
    res = _post(pickup=False, extra={"discount_cents": 7000, "shipping_cents": 0,
                                     "shipping_typed": True})
    assert res["auto_paid"] is True
    _deliver(db, res["order_id"])
    assert _row(db, res["order_id"])["status"] == "delivered"
    assert _biofield_months(db) == 0
    # Control: the same delivery of a paid order with a real method starts the month.
    paid = _insert(db, "INH-PAID-REM", total=7000)
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid', pay_method='Zelle', status='new' "
                   "WHERE id=?", (paid,))
        cx.commit()
    _deliver(db, paid)
    assert _biofield_months(db) == 1


# ── Review finding 5: no reorder-loyalty price, no referral hold ─────────────

def test_a_zero_order_earns_no_reorder_loyalty_price(env, monkeypatch):
    db, calls = env
    monkeypatch.setattr(appmod, "_has_paid_biofield", lambda e: True)
    monkeypatch.setattr(appmod, "_qty_eligible", lambda p: True)
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert appmod._earned_reorder_slugs(cx, EMAIL) == set()
        cx.execute("UPDATE orders SET pay_method='Zelle' WHERE id=?", (res["order_id"],))
        cx.commit()
        assert appmod._earned_reorder_slugs(cx, EMAIL) == {"mix"}   # control


def test_a_zero_order_is_no_purchase_for_the_referral_hold(env):
    db, calls = env
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    with sqlite3.connect(db) as cx:
        assert SR._purchase_times(cx, [EMAIL]) == []
        cx.execute("UPDATE orders SET pay_method='Zelle' WHERE id=?", (res["order_id"],))
        cx.commit()
        assert len(SR._purchase_times(cx, [EMAIL])) == 1            # control


# ── Review finding 6: only a genuine $0 ───────────────────────────────────────

def test_a_total_floored_from_below_zero_stays_unpaid(env):
    db, calls = env
    res = _post(extra={"adjustment_cents": -8000})
    assert res["totals"]["total_cents"] == 0
    assert res["auto_paid"] is False
    assert _unpaid(_row(db, res["order_id"]))
    assert _count(db) == 10


def test_shipping_that_fell_back_to_zero_stays_unpaid(env, monkeypatch):
    db, calls = env

    def boom(*a, **k):
        raise RuntimeError("rate service down")
    monkeypatch.setattr(appmod, "_price_cart", boom)
    res = _post(pickup=False, extra={"discount_cents": 7000})
    assert res["totals"]["shipping_cents"] == 0 and res["totals"]["total_cents"] == 0
    assert res["auto_paid"] is False
    assert _unpaid(_row(db, res["order_id"]))


# ── Membership and Biofield lines ─────────────────────────────────────────────

def test_a_zero_order_with_a_biofield_or_membership_line_stays_unpaid(env):
    """A $0 paid Biofield would anchor the refill cap (_supply_cap_end), so later
    paid refills would earn months against a free order. A membership line is a
    grant in itself. Both stay unpaid for the operator."""
    db, calls = env
    lines = {
        "INH-BF": [{"slug": "mix", "qty": 1, "line_cents": 0},
                   {"slug": "biofield-analysis", "qty": 1, "line_cents": 0}],
        "INH-BF-FOR": [{"slug": "biofield-analysis", "qty": 1, "line_cents": 0,
                        "billed_for": "kid@x.com"}],
        "INH-MS": [{"slug": "membership:month", "qty": 1, "line_cents": 0}],
        "INH-MK": [{"kind": "membership", "tier": "month", "slug": "x", "qty": 1}],
    }
    ids = [_insert(db, ref, items=items) for ref, items in lines.items()]
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        for oid in ids:
            assert O.autopay_zero_total_order(cx, oid) is False
            o = O.get_order(cx, oid)
            assert _unpaid(o) and o["status"] == "proposed"
    assert calls["grant"] == []
    assert _count(db) == 10


def test_a_zero_membership_order_through_the_route_stays_unpaid(env):
    db, calls = env
    from dashboard import membership_products as MP
    fee = MP.get_tier("month")["price_cents"]
    res = _post(lines=[{"slug": "mix", "qty": 1}, {"slug": "membership:month", "qty": 1}],
                extra={"discount_cents": 7000 + fee})
    assert res["totals"]["total_cents"] == 0
    assert any((ln.get("slug") or "").startswith("membership:") for ln in res["lines"])
    assert res["auto_paid"] is False
    assert _unpaid(_row(db, res["order_id"]))
    assert _count(db) == 10


# ── Editing an auto-paid order ────────────────────────────────────────────────

def _edit(oid):
    body = {"customer": {"name": "Staff", "email": EMAIL}, "update_order_id": oid,
            "lines": [{"slug": "mix", "qty": 2}], "pickup": True}
    return appmod.app.test_client().post("/api/orders/manual", json=body)


def test_editing_an_auto_paid_order_is_refused_with_the_way_forward(env):
    db, calls = env
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    before = _row(db, res["order_id"])
    r = _edit(res["order_id"])
    assert r.status_code == 409
    assert r.get_json()["error"] == ("This $0 order was marked paid automatically. "
                                     "Raise a new order for the added items.")
    assert _row(db, res["order_id"]) == before


def test_any_other_paid_order_keeps_the_old_refusal(env):
    db, calls = env
    res = _post()                                        # $70, stays unpaid
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        O.set_order_payment(cx, res["order_id"], method="Zelle", amount_cents=7000)
    r = _edit(res["order_id"])
    assert r.status_code == 409
    assert r.get_json()["error"] == "this order can no longer be updated"


# ── Round 3: every edit path, the defence-in-depth readers, sending ─────────

def _autopaid(db):
    res = _post(extra={"discount_cents": 7000})
    assert res["auto_paid"] is True
    return res["order_id"]


def test_the_edit_route_refuses_an_auto_paid_order(env):
    """Round 3 edited one to 3 bottles through /api/orders/<oid>/edit and got 200."""
    db, calls = env
    oid = _autopaid(db)
    before = _row(db, oid)
    r = appmod.app.test_client().post(f"/api/orders/{oid}/edit", json={
        "lines": [{"slug": "mix", "qty": 3}], "pickup": True})
    assert r.status_code == 409
    assert r.get_json()["error"] == O.ZERO_TOTAL_EDIT_REFUSAL
    assert _row(db, oid) == before
    assert _count(db) == 9


def test_the_edit_route_still_edits_any_other_paid_order(env):
    db, calls = env
    res = _post()
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        O.set_order_payment(cx, res["order_id"], method="Zelle", amount_cents=7000)
    r = appmod.app.test_client().post(f"/api/orders/{res['order_id']}/edit", json={
        "lines": [{"slug": "mix", "qty": 2}], "pickup": True})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_grant_member_access_refuses_before_granting(env):
    db, calls = env
    oid = _autopaid(db)
    r = appmod.app.test_client().post(f"/api/orders/{oid}/grant-member-access", json={})
    assert r.status_code == 409
    assert r.get_json()["error"] == O.ZERO_TOTAL_EDIT_REFUSAL
    with sqlite3.connect(db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0


def test_the_repricer_itself_refuses_an_auto_paid_order(env):
    db, calls = env
    oid = _autopaid(db)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        order = O.get_order(cx, oid)
        with pytest.raises(appmod.CheckoutError):
            appmod._reprice_and_persist_invoice(cx, order, [{"slug": "mix", "qty": 3}],
                                                pickup=True)
    assert _row(db, oid)["total_cents"] == 0


def _zero_biofield(db, ref, method):
    oid = _insert(db, ref, items=[{"slug": "biofield-analysis", "qty": 1,
                                   "unit_cents": 0, "line_cents": 0}])
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE orders SET pay_status='paid', pay_method=?, paid_at=?, "
                   "status='done' WHERE id=?", (method, _iso(-3), oid))
        cx.commit()
    return oid


def test_a_zero_order_never_qualifies_for_a_biofield_month(env):
    db, calls = env
    _zero_biofield(db, "INH-ZB", "zero-total")
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS biofield_month_grants (biofield_ref TEXT "
                   "PRIMARY KEY, email TEXT, delivered_ref TEXT, granted_at TEXT, days INTEGER)")
        cx.commit()
        assert appmod._qualifying_biofield_order(cx, EMAIL) is None
        cx.execute("UPDATE orders SET pay_method='Zelle' WHERE external_ref='INH-ZB'")
        cx.commit()
        got = appmod._qualifying_biofield_order(cx, EMAIL)              # control
        assert got and got["external_ref"] == "INH-ZB"


def test_a_zero_order_never_anchors_the_refill_cap(env):
    db, calls = env
    _zero_biofield(db, "INH-ZC", "zero-total")
    with sqlite3.connect(db) as cx:
        assert appmod._supply_cap_end(cx, EMAIL) is None
        cx.execute("UPDATE orders SET pay_method='Zelle' WHERE external_ref='INH-ZC'")
        cx.commit()
        assert appmod._supply_cap_end(cx, EMAIL) is not None             # control


def test_a_zero_order_seeds_no_repertoire_reorder_price(env):
    db, calls = env
    oid = _autopaid(db)
    with sqlite3.connect(db) as cx:
        assert appmod._order_slugs_since(cx, EMAIL, 90) == []
        cx.execute("UPDATE orders SET pay_method='Zelle' WHERE id=?", (oid,))
        cx.commit()
        assert appmod._order_slugs_since(cx, EMAIL, 90) == ["mix"]       # control


def test_no_invoice_is_sent_for_an_auto_paid_order(env, monkeypatch):
    db, calls = env
    monkeypatch.setenv("INVOICE_PAYLINK_ENABLED", "1")
    monkeypatch.setattr(O, "_invoice_pdf_attachment", lambda order: None)
    oid = _autopaid(db)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="paid automatically"):
            O._send_invoice_exec({"order_id": oid}, {"cx": cx})
    assert calls["email"] == []
    assert not _row(db, oid)["invoice_sent_at"]
    # Control: the same action on an ordinary paid order in 'new' does send.
    res = _post()
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        O.set_order_payment(cx, res["order_id"], method="Zelle", amount_cents=7000)
        O._send_invoice_exec({"order_id": res["order_id"]}, {"cx": cx})
    assert len(calls["email"]) == 1


@pytest.mark.parametrize("status,pay", [("confirmed", None), ("new", "paid")])
def test_no_points_are_debited_for_an_order_not_proposed_and_unpaid(env, status, pay):
    db, calls = env
    with sqlite3.connect(db) as cx:
        P.credit(cx, EMAIL, value_cents=3900, reason="seed", order_ref="SEED")
        cx.commit()
    oid = _insert(db, "INH-PRE", status=status, points_redeemed_cents=3900,
                  discount_cents=3100)
    if pay:
        with sqlite3.connect(db) as cx:
            cx.execute("UPDATE orders SET pay_status=? WHERE id=?", (pay, oid))
            cx.commit()
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert O.autopay_zero_total_order(cx, oid) is False
        assert not P.has_entry(cx, order_ref="INH-PRE", reason="redeem")
        assert P.balance(cx, EMAIL) == 3900


# ── The pages, run under node ────────────────────────────────────────────────

import shutil as _shutil
import subprocess as _subprocess
from pathlib import Path as _Path

_ROOT = _Path(appmod.__file__).resolve().parent
_NODE = _shutil.which("node")
_needs_node = pytest.mark.skipif(not _NODE, reason="node not installed")


def _fn(page, name):
    start = page.index("function %s(" % name)
    if page[max(0, start - 6):start] == "async ":
        start -= 6
    depth, i = 0, page.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(page[i], 0)
        if depth == 0 and page[i] == "}":
            return page[start:i + 1]
        i += 1


_STUBS = r"""
const nodes = {};
const mk = () => ({value:"", textContent:"", innerHTML:"", className:"", checked:false,
                   style:{display:""}, classList:{add(){}, remove(){}}});
const $ = (id) => (nodes[id] = nodes[id] || mk());
const HEADERS = {}, KEY = "k", CUSTOMER_DRAFT_KEY = "d";
let LAST_ORDER = null, INV_STATUS = null, INV_PAY = null, INV_SENT_AT = null, INV_ORDER = null;
let LINES = [{slug:"mix"}], SUBMIT_TOKEN = "t", SHIP_TYPED = false, PAY_ROWS = {};
let REPLY = null; const toasts = [];
const sessionStorage = {removeItem(){}}; const window = {scrollTo(){}};
function toast(m){ toasts.push(m); } function alert(){}
function esc(s){ return String(s); } function dollars(c){ return "$" + (c/100).toFixed(2); }
function invSentStr(){ return ""; } function requestHawaiiShipping(){ return true; }
function linesPayload(){ return []; } function newSubmitToken(){ return "t2"; }
function invTrackOrder(){} function refreshInvStatus(){} function loadReviewHold(){}
function loadClientPrices(){} function setMethod(){} function renderLines(){}
function refreshMembershipOffer(){} function loadPickupDefault(){} function reloadPayments(){}
async function fetch(url, opts){ return {status: 200, json: async () => REPLY}; }
"""


def _node(page_fns, call):
    src = _STUBS + "\n".join(page_fns) + "\n(async () => {\n" + call + "\n})();"
    out = _subprocess.run([_NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _order_page():
    return (_ROOT / "static" / "order-new.html").read_text()


@_needs_node
@pytest.mark.parametrize("auto_paid", [True, False])
def test_the_order_form_state_and_card_follow_auto_paid(auto_paid):
    page = _order_page()
    fns = [_fn(page, n) for n in ("createInvoice", "showInvoice", "refreshInvMethod")]
    reply = {"ok": True, "order_id": 9, "external_ref": "INH-X", "auto_paid": auto_paid,
             "totals": {"total_cents": 0}, "lines": []}
    got = _node(fns, "REPLY = %s; await createInvoice();"
                     "console.log(JSON.stringify({st: INV_STATUS, pay: INV_PAY,"
                     " html: $('invoice').innerHTML, method: $('inv-method').textContent}));"
                % json.dumps(reply))
    if auto_paid:
        assert (got["st"], got["pay"]) == ("new", "paid")
        assert "paid automatically ($0)" in got["html"]
        for action in ("sendInvoice(", "recordPayment(", "orders.confirm"):
            assert action not in got["html"]
    else:
        assert (got["st"], got["pay"]) == ("proposed", "unpaid")
        assert "Proposed invoice" in got["html"]
        for action in ("sendInvoice(", "recordPayment(", "orders.confirm"):
            assert action in got["html"]


@_needs_node
@pytest.mark.parametrize("method,shown", [("zero-total", "none"), ("Zelle", "inline-block")])
def test_edit_mode_hides_send_invoice_for_an_auto_paid_order(method, shown):
    page = _order_page()
    order = {"id": 7, "status": "new", "pay_status": "paid", "pay_method": method,
             "items": [], "address": {}, "email": "a@x.com"}
    got = _node([_fn(page, "loadOrderForEdit")],
                "REPLY = {data: [%s]}; await loadOrderForEdit(7);"
                "console.log(JSON.stringify({send: $('send-inv-btn').style.display}));"
                % json.dumps(order))
    assert got["send"] == shown


@_needs_node
@pytest.mark.parametrize("method,offered", [("zero-total", False), ("Zelle", True)])
def test_the_board_offers_no_send_for_an_auto_paid_order(method, offered):
    page = (_ROOT / "static" / "console-orders.html").read_text()
    got = _node([_fn(page, "sendInvBtn")],
                "console.log(JSON.stringify({b: sendInvBtn({id: 3, pay_method: %s})}));"
                % json.dumps(method))
    assert ("orders.send_invoice" in got["b"]) is offered
