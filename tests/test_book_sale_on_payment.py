"""Regression contract: paid orders stay in-house and never write to QBO."""

import sqlite3

from dashboard import orders as O
from dashboard import qbo_sale


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "orders.db"))
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    return cx


def _seed(cx, external_ref="tok1"):
    oid = O.upsert_order(cx, source="funnel", external_ref=external_ref,
                         email="a@b.com", total_cents=30000)
    O.set_order_qbo_lines(cx, external_ref, {
        "lines": [{"name": "Widget", "amount": 300.0, "qty": 1}]})
    return oid


def test_new_paid_order_never_claims_or_books(tmp_path, monkeypatch):
    cx = _db(tmp_path)
    oid = _seed(cx)
    monkeypatch.setattr(O, "claim_sales_receipt_slot",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("retired booking machinery ran")))
    assert qbo_sale.book_sale_on_payment(cx, O.get_order(cx, oid)) is None
    assert O.get_order(cx, oid)["qbo_sales_receipt_id"] is None


def test_stale_pending_marker_is_not_healed_or_rebooked(tmp_path):
    cx = _db(tmp_path)
    oid = _seed(cx)
    cx.execute("UPDATE orders SET qbo_sales_receipt_id='PENDING' WHERE id=?", (oid,))
    cx.commit()
    assert qbo_sale.book_sale_on_payment(cx, O.get_order(cx, oid)) is None
    assert O.get_order(cx, oid)["qbo_sales_receipt_id"] == "PENDING"


def test_historical_and_deleted_markers_are_preserved():
    assert qbo_sale.book_sale_on_payment(None, {
        "qbo_sales_receipt_id": "24767"}) == "24767"
    assert qbo_sale.book_sale_on_payment(None, {
        "qbo_sales_receipt_id": "DELETED:24767"}) == "DELETED:24767"
