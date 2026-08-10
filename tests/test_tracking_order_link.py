import sqlite3

from dashboard import orders as O
from dashboard import tracking as T


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    T.init_tracking_schema(cx)
    return cx


def _order(cx, ref, *, name="Cyndi O'Brien", email="cyndi@example.com",
           address=None, status="new"):
    return O.upsert_order(
        cx, source="manual", external_ref=ref, name=name, email=email,
        address=({"name": name, "street": "1016 W Chicago Ct",
                  "city": "Chandler", "state": "AZ", "zip": "85224"}
                 if address is None else address),
        status=status)


SHIPMENT = {
    "tracking": "9405530109355381515251",
    "recipient_name": "Cyndi O'Brien",
    "street": "1016 W CHICAGO CT",
    "city": "CHANDLER",
    "state": "AZ",
    "zip": "85224-5249",
}


def test_unique_exact_address_links_tracking_without_marking_shipped():
    cx = _cx()
    oid = _order(cx, "INH-1")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result == {"status": "linked", "order_ids": [oid],
                      "reason": "exact shipping address"}
    order = O.get_order(cx, oid)
    assert order["tracking_number"] == SHIPMENT["tracking"]
    assert order["shipment_id"] == sid
    assert order["status"] == "new"


def test_duplicate_address_uses_exact_email_to_disambiguate():
    cx = _cx()
    wanted = _order(cx, "INH-1", email="cyndi@example.com")
    _order(cx, "INH-2", name="Other Person", email="other@example.com")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "linked"
    assert result["order_ids"] == [wanted]
    assert result["reason"] == "exact address + client email"


def test_ambiguous_match_never_writes_tracking():
    cx = _cx()
    a = _order(cx, "INH-1")
    b = _order(cx, "INH-2")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT)

    assert result["status"] == "ambiguous"
    assert set(result["order_ids"]) == {a, b}
    assert O.get_order(cx, a)["tracking_number"] is None
    assert O.get_order(cx, b)["tracking_number"] is None


def test_unique_email_name_fallback_links_when_old_order_has_no_address():
    cx = _cx()
    oid = _order(cx, "INH-1", address={})
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "linked"
    assert result["order_ids"] == [oid]
    assert result["reason"] == "exact client email + recipient name"


def test_no_open_order_is_audited_as_unmatched():
    cx = _cx()
    _order(cx, "OLD", status="done")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result == {"status": "unmatched", "order_ids": [],
                      "reason": "no safe open-order match"}
    row = cx.execute("SELECT order_link_status, order_link_reason, linked_order_ids "
                     "FROM shipments WHERE id=?", (sid,)).fetchone()
    assert tuple(row) == ("unmatched", "no safe open-order match", "[]")


def test_reprocessing_is_idempotent():
    cx = _cx()
    oid = _order(cx, "INH-1")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")
    first = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                      resolved_email="cyndi@example.com")
    second = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")
    assert first == second
    assert O.get_order(cx, oid)["tracking_number"] == SHIPMENT["tracking"]
