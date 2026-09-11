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
                      "reason": "no safe unlinked-order match"}
    row = cx.execute("SELECT order_link_status, order_link_reason, linked_order_ids "
                     "FROM shipments WHERE id=?", (sid,)).fetchone()
    assert tuple(row) == ("unmatched", "no safe unlinked-order match", "[]")


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


# ── A 'shipped' order is a candidate too (2026-09-10) ────────────────────────
#
# An order reaches 'shipped' when someone records the shipment by hand, which
# routinely happens before the Click-N-Ship confirmation is parsed. While the
# candidate set was ('new','packed'), orders 170 and 146 sat holding no tracking
# number and no shipment_id, so they could never record one and could never
# advance to 'delivered' on a carrier scan. One of them HAD been delivered.


def test_a_shipped_order_with_no_tracking_still_gets_linked():
    """The case that was silently unreachable."""
    cx = _cx()
    oid = _order(cx, "INH-1", status="shipped")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "linked" and result["order_ids"] == [oid]
    order = O.get_order(cx, oid)
    assert order["tracking_number"] == SHIPMENT["tracking"]
    assert order["shipment_id"] == sid
    # Linking still never touches the lifecycle. Buying or recording a label is not
    # carrier acceptance; only a carrier scan advances the card.
    assert order["status"] == "shipped"


def test_a_delivered_order_is_never_a_candidate():
    """Deliberately still excluded: attaching a parcel to a closed order gains
    nothing and would touch a settled record."""
    cx = _cx()
    _order(cx, "INH-1", status="delivered")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "unmatched"


def test_a_done_order_is_never_a_candidate():
    cx = _cx()
    _order(cx, "INH-1", status="done")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    assert T.link_shipment_to_orders(
        cx, sid, SHIPMENT, resolved_email="cyndi@example.com")["status"] == "unmatched"


def test_an_order_that_already_has_tracking_is_never_overwritten():
    """The guard that makes widening safe. Pinned explicitly because the whole
    argument for adding 'shipped' rests on it."""
    cx = _cx()
    oid = _order(cx, "INH-1", status="shipped")
    O.set_order_tracking(cx, oid, "9400000000000000000000")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "unmatched"
    assert O.get_order(cx, oid)["tracking_number"] == "9400000000000000000000"


def test_two_shipped_orders_at_one_address_refuse_rather_than_guess():
    """The hazard widening introduces: more candidates means more chance of a
    collision. It must degrade to a refusal, never a mis-assignment."""
    cx = _cx()
    a = _order(cx, "INH-1", status="shipped", name="Cyndi O'Brien",
               email="cyndi@example.com")
    b = _order(cx, "INH-2", status="shipped", name="Cyndi O'Brien",
               email="cyndi@example.com")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "ambiguous"
    assert sorted(result["order_ids"]) == sorted([a, b])
    for oid in (a, b):
        assert not (O.get_order(cx, oid)["tracking_number"] or "")


def test_a_new_and_a_shipped_order_at_one_address_also_refuse():
    """Mixing stages must not make one of them win by accident."""
    cx = _cx()
    a = _order(cx, "INH-1", status="new")
    b = _order(cx, "INH-2", status="shipped")
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="drafted")

    result = T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                       resolved_email="cyndi@example.com")

    assert result["status"] == "ambiguous"
    for oid in (a, b):
        assert not (O.get_order(cx, oid)["tracking_number"] or "")
