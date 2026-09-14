"""Selection rules for post-purchase review invites.

The invite fires N days after an order SHIPS, never on payment: a buyer cannot
review a product that has not arrived. `max_age_days` is the backlog guard, so
switching REVIEWS_ENABLED on does not email every historical buyer at once.
"""
import json
import sqlite3
from datetime import datetime, timedelta

from dashboard import orders as _orders
from dashboard import review_invites as ri


def _cx():
    cx = sqlite3.connect(":memory:")
    _orders.init_orders_table(cx)
    ri.init_table(cx)
    return cx


def _ago(days):
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _order(cx, *, email="b@x.com", slugs=("wholomega",), status="shipped",
           updated_days_ago=20, external_ref="o1", name="Buyer"):
    items = [{"slug": s, "name": s, "qty": 1} for s in slugs]
    cur = cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, email, name, "
        "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (_ago(updated_days_ago + 2), "test", external_ref, email, name,
         json.dumps(items), status, _ago(updated_days_ago)))
    cx.commit()
    return cur.lastrowid


def test_pending_selects_shipped_order_past_the_delay():
    cx = _cx()
    _order(cx, updated_days_ago=20)
    rows = ri.pending(cx, days=14)
    assert len(rows) == 1
    assert rows[0]["email"] == "b@x.com"
    assert rows[0]["slug"] == "wholomega"
    assert rows[0]["name"] == "Buyer"


def test_pending_excludes_an_order_still_inside_the_delay():
    cx = _cx()
    _order(cx, updated_days_ago=3)
    assert ri.pending(cx, days=14) == []


def test_pending_excludes_an_unshipped_order():
    cx = _cx()
    _order(cx, status="new", updated_days_ago=20)
    assert ri.pending(cx, days=14) == []


def test_pending_excludes_a_cancelled_order():
    cx = _cx()
    _order(cx, status="cancelled", updated_days_ago=20)
    assert ri.pending(cx, days=14) == []


def test_delivered_and_done_also_qualify():
    cx = _cx()
    _order(cx, status="delivered", external_ref="o1", email="d@x.com")
    _order(cx, status="done", external_ref="o2", email="e@x.com")
    got = {r["email"] for r in ri.pending(cx, days=14)}
    assert got == {"d@x.com", "e@x.com"}


def test_max_age_excludes_the_historical_backlog():
    cx = _cx()
    _order(cx, updated_days_ago=200)
    assert ri.pending(cx, days=14, max_age_days=60) == []


def test_one_row_per_product_in_a_multi_product_order():
    cx = _cx()
    _order(cx, slugs=("wholomega", "lipid-zyme"))
    rows = ri.pending(cx, days=14)
    assert {r["slug"] for r in rows} == {"wholomega", "lipid-zyme"}


def test_mark_invited_makes_it_idempotent():
    cx = _cx()
    oid = _order(cx)
    rows = ri.pending(cx, days=14)
    assert len(rows) == 1
    ri.mark_invited(cx, "b@x.com", "wholomega", oid)
    assert ri.pending(cx, days=14) == []


def test_mark_invited_twice_does_not_raise():
    cx = _cx()
    oid = _order(cx)
    ri.mark_invited(cx, "b@x.com", "wholomega", oid)
    ri.mark_invited(cx, "b@x.com", "wholomega", oid)
    assert ri.pending(cx, days=14) == []


def test_a_second_order_of_the_same_product_is_not_re_invited():
    cx = _cx()
    oid = _order(cx, external_ref="o1")
    ri.mark_invited(cx, "b@x.com", "wholomega", oid)
    _order(cx, external_ref="o2")
    assert ri.pending(cx, days=14) == []


def test_pending_excludes_a_blank_email():
    cx = _cx()
    _order(cx, email="")
    assert ri.pending(cx, days=14) == []


def test_pending_skips_an_item_with_no_slug():
    cx = _cx()
    cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, email, name, "
        "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (_ago(22), "test", "o1", "b@x.com", "Buyer",
         json.dumps([{"name": "hand typed line", "qty": 1}]), "shipped", _ago(20)))
    cx.commit()
    assert ri.pending(cx, days=14) == []


def test_pending_survives_unparseable_items_json():
    cx = _cx()
    cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, email, name, "
        "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (_ago(22), "test", "o1", "b@x.com", "Buyer", "{not json", "shipped", _ago(20)))
    cx.commit()
    assert ri.pending(cx, days=14) == []


def test_limit_is_respected():
    cx = _cx()
    for i in range(5):
        _order(cx, email=f"b{i}@x.com", external_ref=f"o{i}")
    assert len(ri.pending(cx, days=14, limit=3)) == 3


def test_editing_an_old_shipped_order_cannot_produce_a_second_invite():
    """`updated_at` is the ship-time proxy and ANY later edit moves it. That can pull
    an old order back into the window, so the (email, slug) key is what actually
    protects the buyer from being asked twice."""
    cx = _cx()
    oid = _order(cx, updated_days_ago=20)
    ri.mark_invited(cx, "b@x.com", "wholomega", oid)
    # Rae edits the order today; updated_at resets and it re-enters the window later.
    cx.execute("UPDATE orders SET updated_at=? WHERE id=?", (_ago(20), oid))
    cx.commit()
    assert ri.pending(cx, days=14) == []


# ── Holds: an order or one product on it that must not be asked about ─────────
# A buyer who reported a missing item was asked to review that item on 2026-09-13.
# Nothing on an order records a complaint, so a hold is set by hand.

def test_a_held_order_gets_no_invite():
    cx = _cx()
    oid = _order(cx, slugs=("wholomega", "lipid-zyme"))
    ri.hold(cx, oid, reason="missing item")
    assert ri.pending(cx, days=14) == []


def test_a_held_product_skips_only_that_product():
    cx = _cx()
    oid = _order(cx, slugs=("wholomega", "lipid-zyme"))
    ri.hold(cx, oid, slug="wholomega", reason="bottle never arrived")
    assert [r["slug"] for r in ri.pending(cx, days=14)] == ["lipid-zyme"]


def test_a_hold_is_scoped_to_its_order():
    cx = _cx()
    held = _order(cx, external_ref="o1", email="a@x.com")
    _order(cx, external_ref="o2", email="c@x.com")
    ri.hold(cx, held, reason="missing item")
    assert [r["email"] for r in ri.pending(cx, days=14)] == ["c@x.com"]


def test_releasing_a_hold_restores_the_invite():
    """A hold must not stamp the pair as invited, or releasing it could never send."""
    cx = _cx()
    oid = _order(cx)
    ri.hold(cx, oid, slug="wholomega")
    assert ri.pending(cx, days=14) == []
    ri.release(cx, oid, slug="wholomega")
    assert [r["slug"] for r in ri.pending(cx, days=14)] == ["wholomega"]


def test_holding_twice_keeps_one_row_and_the_latest_reason():
    cx = _cx()
    oid = _order(cx)
    ri.hold(cx, oid, reason="first")
    ri.hold(cx, oid, reason="second")
    got = ri.holds_for(cx, oid)
    assert len(got) == 1
    assert got[0]["slug"] == "" and got[0]["reason"] == "second"


def test_releasing_a_hold_that_does_not_exist_does_not_raise():
    cx = _cx()
    oid = _order(cx)
    ri.release(cx, oid, slug="wholomega")
    assert ri.holds_for(cx, oid) == []


# ── Practitioners' clients ────────────────────────────────────────────────────
# Glen 2026-09-13: a practitioner switch, off by default, and the CLIENT gets the
# email. A drop-ship order carries the practitioner's email, so it goes to the
# client email captured at checkout, or not at all.

from dashboard import practitioner_settings as ps  # noqa: E402


def _client_order(cx, *, source, pid="p1", email="prac@x.com", recipient_email=None,
                  ship_name="Tony Client", slugs=("esr",), external_ref="c1"):
    items = [{"slug": s, "name": s, "qty": 1} for s in slugs]
    cur = cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, email, name, items_json, "
        "status, updated_at, practitioner_id, recipient_email, address_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_ago(22), source, external_ref, email, "Practitioner Name", json.dumps(items),
         "delivered", _ago(20), pid, recipient_email, json.dumps({"name": ship_name})))
    cx.commit()
    return cur.lastrowid


def _switch(cx, pid, on):
    ps.init_settings_table(cx)
    ps.set_client_review_emails(cx, pid, on)


def test_the_practitioner_switch_is_off_by_default():
    cx = _cx()
    ps.init_settings_table(cx)
    assert ps.client_review_emails_enabled(cx, "p1") is False


def test_a_store_order_waits_for_the_practitioner_switch():
    cx = _cx()
    _client_order(cx, source="dispensary", email="client@x.com")
    assert ri.pending(cx, days=14) == []
    _switch(cx, "p1", True)
    assert [r["email"] for r in ri.pending(cx, days=14)] == ["client@x.com"]


def test_a_practitioner_order_goes_to_the_client_when_switched_on():
    cx = _cx()
    _client_order(cx, source="dropship", recipient_email="tony@x.com")
    assert ri.pending(cx, days=14) == []
    _switch(cx, "p1", True)
    rows = ri.pending(cx, days=14)
    assert [(r["email"], r["name"], r["slug"]) for r in rows] == [
        ("tony@x.com", "Tony Client", "esr")]


def test_a_practitioner_order_without_a_client_email_sends_nothing():
    cx = _cx()
    _client_order(cx, source="dropship", recipient_email=None)
    _switch(cx, "p1", True)
    assert ri.pending(cx, days=14) == []


def test_a_practitioner_order_never_emails_the_practitioner():
    cx = _cx()
    _client_order(cx, source="dropship", recipient_email="PRAC@x.com ")
    _switch(cx, "p1", True)
    assert ri.pending(cx, days=14) == []


def test_the_switch_belongs_to_one_practitioner():
    cx = _cx()
    _client_order(cx, source="dispensary", pid="p1", email="a@x.com", external_ref="c1")
    _client_order(cx, source="dispensary", pid="p2", email="b@x.com", external_ref="c2")
    _switch(cx, "p2", True)
    assert [r["email"] for r in ri.pending(cx, days=14)] == ["b@x.com"]


def test_turning_the_switch_off_stops_the_invites():
    cx = _cx()
    _client_order(cx, source="dispensary", email="client@x.com")
    _switch(cx, "p1", True)
    _switch(cx, "p1", False)
    assert ri.pending(cx, days=14) == []


def test_upsert_order_records_the_client_email():
    cx = _cx()
    oid = _orders.upsert_order(cx, source="dropship", external_ref="INV9",
                               email="prac@x.com", name="Prac",
                               recipient_email="tony@x.com")
    got = cx.execute("SELECT recipient_email FROM orders WHERE id=?", (oid,)).fetchone()
    assert got[0] == "tony@x.com"
