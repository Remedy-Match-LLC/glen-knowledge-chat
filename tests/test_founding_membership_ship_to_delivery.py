"""Founding membership runs from shipment to 30 days after delivery (Glen, 2026-10-14).

Reservation grants nothing (see test_founding_checkout_return). The ship charge
opens 30 days and records the order. The first delivery of that order moves the
end to delivery + 30 days. Nothing here ever shortens a membership.
"""
import sqlite3
from datetime import datetime, timedelta

import app as appmod
from dashboard import founding, subscriptions as subs

EMAIL = "f@x.com"


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    subs.init_subscriptions_table(cx)
    subs.migrate_add_founding_columns(cx)
    subs.migrate_add_failed_count(cx)
    cx.execute("CREATE TABLE memberships (id TEXT, email TEXT, granted_at TEXT, expires_at TEXT,"
               " granted_by TEXT, source TEXT, truly_vip_ref TEXT, notes TEXT)")
    cx.commit()
    return cx


def _expiry(cx, email=EMAIL):
    v = cx.execute("SELECT MAX(expires_at) FROM memberships WHERE email=?", (email,)).fetchone()[0]
    return datetime.fromisoformat(v.rstrip("Z")) if v else None


def _iso(dt):
    return dt.isoformat() + "Z"


def _ship(cx, monkeypatch, *, status="succeeded", seen=None):
    sid = subs.create_founding_reservation(
        cx, email=EMAIL, stripe_customer_id="cus_1", stripe_payment_method_id="pm_1",
        items=[{"slug": "neuro-magnesium", "qty": 1}], ship_address={"state": "HI"},
        founding_slug="neuro-magnesium")

    def _price(items, *, ship, subscriber_tier_pct=None, **k):
        if seen is not None:
            seen.append(k.get("program_member"))
        return {"qbo_lines": [], "shipping_cents": 600, "discount_cents": 0,
                "points_redeemed_cents": 0,
                "priced": {"total_cents": 8000, "get_cents": 0, "subtotal_cents": 8000}}
    monkeypatch.setattr(appmod, "_price_cart", _price)
    monkeypatch.setattr(appmod.stripe_pay, "charge_off_session",
                        lambda *a, **k: {"status": status, "id": "pi_1"})
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: None)
    monkeypatch.setattr(appmod, "_member_join_welcome", lambda *a, **k: None)
    return appmod._ship_founding_reservation(cx, subs.get(cx, sid))


def test_ship_opens_thirty_days_and_records_the_order(monkeypatch):
    cx = _cx()
    seen = []
    before = datetime.utcnow()
    res = _ship(cx, monkeypatch, seen=seen)
    assert res["charged"] is True
    exp = _expiry(cx)
    assert exp is not None
    assert before + timedelta(days=29, hours=23) <= exp <= datetime.utcnow() + timedelta(days=30, minutes=1)
    row = cx.execute("SELECT * FROM founding_ship_memberships").fetchone()
    assert row["order_ref"] == "pi_1" and row["email"] == EMAIL and row["delivered_at"] is None
    assert seen == [True]   # priced as a member: the membership starts with this shipment


def test_failed_ship_charge_grants_nothing(monkeypatch):
    cx = _cx()
    res = _ship(cx, monkeypatch, status="requires_action")
    assert res["charged"] is False
    assert _expiry(cx) is None
    founding.init_ship_memberships(cx)
    assert cx.execute("SELECT COUNT(*) FROM founding_ship_memberships").fetchone()[0] == 0


def test_delivery_ends_membership_thirty_days_after_delivery(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    delivered = datetime.utcnow().replace(microsecond=0) + timedelta(days=6)
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, _iso(delivered)) == "extended"
    assert _expiry(cx) == delivered + timedelta(days=30)


def test_second_delivery_signal_does_not_move_the_end(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    first = datetime.utcnow().replace(microsecond=0) + timedelta(days=4)
    appmod._extend_founding_membership_on_delivery(cx, {"external_ref": "pi_1"}, _iso(first))
    later = first + timedelta(days=9)
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, _iso(later)) == "none"
    assert _expiry(cx) == first + timedelta(days=30)


def test_other_orders_do_not_extend(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    end = _expiry(cx)
    far = datetime.utcnow() + timedelta(days=20)
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_other"}, _iso(far)) == "none"
    assert appmod._extend_founding_membership_on_delivery(cx, {}, _iso(far)) == "none"
    assert _expiry(cx) == end


def test_delivery_never_shortens_a_longer_membership(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    longer = datetime.utcnow().replace(microsecond=0) + timedelta(days=200)
    cx.execute("INSERT INTO memberships (id, email, expires_at, source) VALUES ('y', ?, ?, 'annual')",
               (EMAIL, _iso(longer)))
    appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, _iso(datetime.utcnow() + timedelta(days=3)))
    assert _expiry(cx) == longer


def test_unreadable_delivery_time_falls_back_to_now(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    cx.execute("DELETE FROM memberships")
    before = datetime.utcnow()
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, "not a date") == "extended"
    exp = _expiry(cx)
    assert before + timedelta(days=30) <= exp <= datetime.utcnow() + timedelta(days=30, minutes=1)


def test_failure_keeps_the_callers_delivery_work(monkeypatch):
    """A failing extension rolls back only itself, not the delivery marks before it."""
    cx = _cx()
    _ship(cx, monkeypatch)
    cx.execute("CREATE TABLE marks (v TEXT)")
    cx.commit()
    cx.execute("INSERT INTO marks VALUES ('delivered')")   # caller's unsaved write

    def _boom(*a, **k):
        raise RuntimeError("grant failed")
    monkeypatch.setattr(appmod, "_extend_membership_grant", _boom)
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, _iso(datetime.utcnow())) == "error"
    assert cx.execute("SELECT v FROM marks").fetchall()[0][0] == "delivered"
    # The claim rolled back too, so a later signal can still set the end.
    assert cx.execute("SELECT delivered_at FROM founding_ship_memberships").fetchone()[0] is None


def test_shipment_delivery_runs_the_founding_extension_without_a_member_email(monkeypatch):
    """The carrier path reaches the founding extension even when the order has no email."""
    cx = _cx()
    calls = []
    monkeypatch.setattr(appmod._tracking, "mark_shipment_delivered", lambda *a, **k: True)
    monkeypatch.setattr(appmod._coaching, "shipment_member_orders",
                        lambda cx, sid, uuid: [{"id": 7, "email": "", "source": "reorder"}])
    monkeypatch.setattr(appmod._bos_orders, "set_order_status", lambda *a, **k: True)
    monkeypatch.setattr(appmod._bos_orders, "get_order",
                        lambda cx, oid: {"id": oid, "external_ref": "pi_1"})
    monkeypatch.setattr(appmod, "_extend_founding_membership_on_delivery",
                        lambda cx, order, at: calls.append((order["external_ref"], at)))
    appmod._activate_coaching_for_shipment(
        cx, {"id": 1, "order_uuid": "u", "delivered_at": None, "coaching_opened": 0,
             "resolved_email": None}, delivered_at="2026-10-20T10:00:00Z")
    assert calls == [("pi_1", "2026-10-20T10:00:00Z")]


def test_offset_delivery_time_is_read_as_utc(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    local = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)
    stamp = local.isoformat() + "-10:00"          # Hawaii time: ten hours behind UTC
    appmod._extend_founding_membership_on_delivery(cx, {"external_ref": "pi_1"}, stamp)
    assert _expiry(cx) == local + timedelta(hours=10, days=30)


def test_stored_delivery_time_is_the_one_used(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    appmod._extend_founding_membership_on_delivery(cx, {"external_ref": "pi_1"}, "not a date")
    stored = cx.execute("SELECT delivered_at FROM founding_ship_memberships").fetchone()[0]
    assert stored != "not a date"
    assert _expiry(cx) >= datetime.fromisoformat(stored.rstrip("Z")) + timedelta(days=30)


def test_shipping_the_same_order_twice_grants_once(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    sub = dict(subs.get(cx, 1))
    appmod._start_founding_membership_on_ship(cx, sub, "pi_1")
    assert cx.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 1


def test_failed_grant_keeps_the_order_so_delivery_still_sets_the_end(monkeypatch):
    cx = _cx()

    def _boom(*a, **k):
        raise RuntimeError("grant failed")
    monkeypatch.setattr(appmod, "_grant_membership", _boom)
    res = _ship(cx, monkeypatch)
    assert res["charged"] is True
    assert _expiry(cx) is None
    delivered = datetime.utcnow().replace(microsecond=0) + timedelta(days=3)
    assert appmod._extend_founding_membership_on_delivery(
        cx, {"external_ref": "pi_1"}, _iso(delivered)) == "extended"
    assert _expiry(cx) == delivered + timedelta(days=30)


def test_ship_bookkeeping_survives_a_failed_grant(monkeypatch):
    """The charge happened, so what the ship path wrote must be committed."""
    cx = _cx()
    cx.execute("CREATE TABLE marks (v TEXT)")
    cx.commit()
    cx.execute("INSERT INTO marks VALUES ('booked')")   # unsaved ship bookkeeping

    def _boom(*a, **k):
        raise RuntimeError("grant failed")
    monkeypatch.setattr(appmod, "_grant_membership", _boom)
    appmod._start_founding_membership_on_ship(cx, {"id": 9, "email": EMAIL}, "")   # no order ref: nothing else commits
    cx.rollback()
    assert cx.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 1


def test_membership_exists_before_the_order_row_is_created(monkeypatch):
    """Coaching on delivery needs a membership that covered the order's creation time."""
    cx = _cx()
    at_ingest = []
    _ship(cx, monkeypatch)   # sets the stubs; re-run below with a capturing ingest
    cx.execute("DELETE FROM memberships")
    cx.execute("DELETE FROM founding_ship_memberships")
    cx.execute("UPDATE subscriptions SET founding_state='pending'")
    cx.commit()
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: at_ingest.append(_expiry(cx)))
    appmod._ship_founding_reservation(cx, subs.get(cx, 1))
    assert at_ingest and at_ingest[0] is not None


def test_reservation_card_delivery_sets_the_end(monkeypatch):
    """Fulfilment may ship against the $0 card made at reservation."""
    cx = _cx()
    _ship(cx, monkeypatch)
    delivered = datetime.utcnow().replace(microsecond=0) + timedelta(days=4)
    card = {"external_ref": "seti_1", "source": "founding", "email": EMAIL}
    assert appmod._extend_founding_membership_on_delivery(cx, card, _iso(delivered)) == "extended"
    assert _expiry(cx) == delivered + timedelta(days=30)


def test_other_cards_for_the_founder_do_not_set_the_end(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    end = _expiry(cx)
    other = {"external_ref": "pi_later", "source": "reorder", "email": EMAIL}
    assert appmod._extend_founding_membership_on_delivery(
        cx, other, _iso(datetime.utcnow() + timedelta(days=4))) == "none"
    assert _expiry(cx) == end


def test_reservation_card_for_someone_else_does_nothing(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    end = _expiry(cx)
    card = {"external_ref": "seti_9", "source": "founding", "email": "other@x.com"}
    assert appmod._extend_founding_membership_on_delivery(
        cx, card, _iso(datetime.utcnow() + timedelta(days=4))) == "none"
    assert _expiry(cx) == end


def test_mark_delivered_without_a_shipment_runs_the_extension(monkeypatch):
    cx = _cx()
    cx.execute("CREATE TABLE orders (id INTEGER, email TEXT, source TEXT, shipment_id INTEGER)")
    cx.execute("INSERT INTO orders VALUES (5, ?, 'reorder', NULL)", (EMAIL,))
    calls = []
    monkeypatch.setattr(appmod._bos_orders, "get_order",
                        lambda cx, oid: {"id": oid, "external_ref": "pi_1"})
    monkeypatch.setattr(appmod, "_extend_founding_membership_on_delivery",
                        lambda cx, order, at: calls.append(order["external_ref"]))
    monkeypatch.setattr(appmod, "_open_coaching_for_order", lambda *a, **k: {"ok": True})
    appmod._activate_coaching_by_order(cx, 5)
    assert calls == ["pi_1"]


def test_reservation_card_with_no_ref_does_nothing(monkeypatch):
    cx = _cx()
    _ship(cx, monkeypatch)
    end = _expiry(cx)
    card = {"external_ref": "", "source": "founding", "email": EMAIL}
    assert appmod._extend_founding_membership_on_delivery(
        cx, card, _iso(datetime.utcnow() + timedelta(days=4))) == "none"
    assert _expiry(cx) == end


def test_two_bottles_reservation_cards_settle_oldest_first():
    cx = _cx()
    founding.record_ship(cx, order_ref="pi_a", sub_id=1, email=EMAIL, shipped_at="2026-11-01T00:00:00Z")
    founding.record_ship(cx, order_ref="pi_b", sub_id=2, email=EMAIL, shipped_at="2026-12-01T00:00:00Z")
    assert founding.claim_delivery_by_email(cx, EMAIL, "2026-11-05T00:00:00Z") == EMAIL
    assert founding.claim_delivery_by_email(cx, EMAIL, "2026-12-05T00:00:00Z") == EMAIL
    got = dict(cx.execute("SELECT order_ref, delivered_at FROM founding_ship_memberships").fetchall())
    assert got == {"pi_a": "2026-11-05T00:00:00Z", "pi_b": "2026-12-05T00:00:00Z"}


def test_mixed_case_email_still_matches(monkeypatch):
    cx = _cx()
    appmod._start_founding_membership_on_ship(cx, {"id": 3, "email": " F@X.com "}, "pi_3")
    card = {"external_ref": "seti_3", "source": "founding", "email": "f@x.COM"}
    assert appmod._extend_founding_membership_on_delivery(
        cx, card, _iso(datetime.utcnow() + timedelta(days=4))) == "extended"


def test_founder_gets_a_people_record(monkeypatch):
    from dashboard import customers
    cx = _cx()
    seen = []
    monkeypatch.setattr(customers, "find_or_create_by_email", lambda cx, email: seen.append(email))
    monkeypatch.setattr(appmod, "_member_join_welcome", lambda *a, **k: None)
    appmod._start_founding_membership_on_ship(cx, {"id": 4, "email": EMAIL}, "pi_4")
    assert seen == [EMAIL]


def test_delivery_end_is_saved(monkeypatch, tmp_path):
    """The end date must be committed, not left on the caller's open transaction."""
    path = str(tmp_path / "f.db")
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE memberships (id TEXT, email TEXT, granted_at TEXT, expires_at TEXT,"
               " granted_by TEXT, source TEXT, truly_vip_ref TEXT, notes TEXT)")
    founding.record_ship(cx, order_ref="pi_1", sub_id=1, email=EMAIL, shipped_at="2026-11-01T00:00:00Z")
    cx.commit()
    # Open a transaction first, as a pooled Postgres connection always has one.
    # Otherwise SQLite's outermost RELEASE commits by itself and hides a missing commit.
    cx.execute("INSERT INTO memberships (id, email) VALUES ('mark', 'caller@x.com')")
    delivered = datetime.utcnow().replace(microsecond=0) + timedelta(days=4)
    appmod._extend_founding_membership_on_delivery(cx, {"external_ref": "pi_1"}, _iso(delivered))
    other = sqlite3.connect(path)
    assert _expiry(other) == delivered + timedelta(days=30)


class _Recorder:
    """A connection that fails one statement and records rollbacks, as Postgres needs."""
    def __init__(self, cx, fail_on):
        self._cx, self._fail_on, self.rollbacks = cx, fail_on, 0

    def execute(self, sql, *a):
        if self._fail_on in sql:
            raise RuntimeError("statement failed")
        return self._cx.execute(sql, *a)

    def commit(self):
        self._cx.commit()

    def rollback(self):
        self.rollbacks += 1
        self._cx.rollback()


def test_a_failed_ship_step_is_rolled_back_before_the_next(monkeypatch):
    cx = _Recorder(_cx(), fail_on="founding_ship_memberships (order_ref")
    monkeypatch.setattr(appmod, "_member_join_welcome", lambda *a, **k: None)
    appmod._start_founding_membership_on_ship(cx, {"id": 5, "email": EMAIL}, "pi_5")
    assert cx.rollbacks >= 1
    assert _expiry(cx._cx) is not None      # the grant still went through
