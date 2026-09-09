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
