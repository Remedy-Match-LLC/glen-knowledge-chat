"""Who set an order's shipping figure (Glen 2026-11-14: a customer edit recalculates;
a figure the owner typed is kept)."""
import sqlite3

from dashboard import orders as o


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "o.db"))
    cx.row_factory = sqlite3.Row
    o.init_orders_table(cx)
    return cx


def test_upsert_records_and_keeps_the_flag(tmp_path):
    cx = _cx(tmp_path)
    oid = o.upsert_order(cx, source="in-house", external_ref="R1", shipping_cents=3300,
                         shipping_hand_set=True)
    assert o.get_order(cx, oid)["shipping_hand_set"] == 1
    o.upsert_order(cx, source="in-house", external_ref="R1", shipping_cents=3300)
    assert o.get_order(cx, oid)["shipping_hand_set"] == 1      # None leaves it
    o.upsert_order(cx, source="in-house", external_ref="R1", shipping_cents=700,
                   shipping_hand_set=False)
    assert o.get_order(cx, oid)["shipping_hand_set"] == 0


def test_an_order_saved_before_the_flag_reads_as_computed(tmp_path):
    cx = _cx(tmp_path)
    oid = o.upsert_order(cx, source="in-house", external_ref="R2", shipping_cents=3200)
    assert o.get_order(cx, oid)["shipping_hand_set"] is None


def test_monas_300_is_marked_only_while_it_is_still_33(tmp_path):
    cx = _cx(tmp_path)
    cx.execute("INSERT INTO orders (id, created_at, source, external_ref, shipping_cents) "
               "VALUES (300, '2026-11-01', 'in-house', 'M', 3300)")
    cx.execute("INSERT INTO orders (id, created_at, source, external_ref, shipping_cents) "
               "VALUES (301, '2026-11-01', 'in-house', 'N', 3300)")
    cx.commit()
    o.init_orders_table(cx)
    assert o.get_order(cx, 300)["shipping_hand_set"] == 1
    assert o.get_order(cx, 301)["shipping_hand_set"] is None
    cx.execute("UPDATE orders SET shipping_hand_set=0 WHERE id=300"); cx.commit()
    o.init_orders_table(cx)
    assert o.get_order(cx, 300)["shipping_hand_set"] == 0          # never re-marked
