"""A parcel links to an order closed before its label, and a drop-ship's tracking goes
to the practitioner.

Fulfillment, 2 Nov 2026: orders 277 and 278 were marked done before their labels were
bought, and 273 was still 'confirmed', so none got a tracking number or email. Glen,
2026-11-02, platform tab: "yes", for a done order paid in the last 14 days that holds a
physical item, and a delivered scan does for it what it does for an open order.

Glen, 2026-11-02, relayed by the primary: "For drop ship orders, such as from Ashley
King, we send the tracking email to the practitioner as she has the email contact."
Order 270 was a done drop-ship, so the board missed it and its tracking was held.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import orders as O
from dashboard import tracking as T
from dashboard import tracking_recipient as tr
from cns_tracking_watcher import handle_confirmation
from tests.test_cns_watcher import ONE_SHIPMENT   # Cyndi O'Brien, 1016 W Chicago Ct 85224
from tests.test_cns_watcher_address_first import (
    AT_LABEL, CLIENT, PRAC, _drafts, _match, _sends, _with_dropship)

PHYSICAL = "brain-cleanse"
DIGITAL = "book-dry-eye-relief-ebook"

SHIPMENT = {"tracking": "9405530109355381515251", "recipient_name": "Cyndi O'Brien",
            "street": "1016 W CHICAGO CT", "city": "CHANDLER", "state": "AZ",
            "zip": "85224-5249"}


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    T.init_tracking_schema(cx)
    return cx


def _order(cx, *, status, paid_days_ago=3, slug=PHYSICAL):
    oid = O.upsert_order(
        cx, source="manual", external_ref=f"INH-{status}-{slug}", name="Cyndi O'Brien",
        email="cyndi@example.com", status=status,
        address={"name": "Cyndi O'Brien", "street": "1016 W Chicago Ct",
                 "city": "Chandler", "state": "AZ", "zip": "85224"})
    cx.execute("UPDATE orders SET status=?, paid_at=?, items_json=? WHERE id=?",
               (status, _days_ago(paid_days_ago) if paid_days_ago is not None else None,
                json.dumps([{"slug": slug, "qty": 1}]), oid))
    cx.commit()
    return oid


def _link(cx):
    sid = T.record_shipment(cx, tracking_number=SHIPMENT["tracking"], status="sent")
    return sid, T.link_shipment_to_orders(cx, sid, SHIPMENT,
                                          resolved_email="cyndi@example.com")


@pytest.mark.parametrize("status", ["confirmed", "paid"])
def test_an_order_before_the_board_gets_its_tracking(status):
    """Order 273 was 'confirmed' when its label was bought."""
    cx = _cx()
    oid = _order(cx, status=status, paid_days_ago=None)
    sid, res = _link(cx)
    assert res["status"] == "linked" and res["order_ids"] == [oid]
    assert O.get_order(cx, oid)["tracking_number"] == SHIPMENT["tracking"]


def test_a_recently_paid_done_order_gets_its_tracking():
    """Orders 277 and 278: closed before the label."""
    cx = _cx()
    oid = _order(cx, status="done", paid_days_ago=3)
    sid, res = _link(cx)
    assert res["status"] == "linked" and res["order_ids"] == [oid]
    row = O.get_order(cx, oid)
    assert row["tracking_number"] == SHIPMENT["tracking"] and row["shipment_id"] == sid
    assert row["status"] == "done"          # buying a label moves no card


@pytest.mark.parametrize("paid_days_ago", [15, 60, None])
def test_an_old_or_unpaid_done_order_is_left_alone(paid_days_ago):
    cx = _cx()
    oid = _order(cx, status="done", paid_days_ago=paid_days_ago)
    _, res = _link(cx)
    assert res["status"] == "unmatched"
    assert O.get_order(cx, oid)["tracking_number"] is None


def test_a_done_order_with_nothing_to_ship_is_left_alone():
    """A digital purchase is created 'done' and ships nothing."""
    cx = _cx()
    oid = _order(cx, status="done", slug=DIGITAL)
    _, res = _link(cx)
    assert res["status"] == "unmatched"
    assert O.get_order(cx, oid)["tracking_number"] is None


def test_a_delivered_order_is_still_left_alone():
    cx = _cx()
    oid = _order(cx, status="delivered")
    _, res = _link(cx)
    assert res["status"] == "unmatched"
    assert O.get_order(cx, oid)["tracking_number"] is None


def test_a_done_order_never_overwrites_its_tracking_number():
    cx = _cx()
    oid = _order(cx, status="done")
    cx.execute("UPDATE orders SET tracking_number='9400000000000000000001' WHERE id=?", (oid,))
    cx.commit()
    _, res = _link(cx)
    assert res["status"] == "unmatched"
    assert O.get_order(cx, oid)["tracking_number"] == "9400000000000000000001"


def test_the_grace_period_is_measured_from_today():
    order = {"status": "done", "paid_at": "2026-11-01T10:00:00Z",
             "items_json": json.dumps([{"slug": PHYSICAL, "qty": 1}])}
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert tr.could_await_label(order, now=now)
    assert tr.could_await_label(order, now=now + timedelta(days=13))      # 15 Nov
    assert not tr.could_await_label(order, now=now + timedelta(days=14))  # 16 Nov


@pytest.mark.parametrize("paid_at", ["2026-2-01", "11/01/2026", "2026-12-25T00:00:00Z",
                                     "", None])
def test_a_garbled_or_future_payment_date_is_not_recent(paid_at):
    order = {"status": "done", "paid_at": paid_at,
             "items_json": json.dumps([{"slug": PHYSICAL, "qty": 1}])}
    assert not tr.could_await_label(order, now=datetime(2026, 11, 2, tzinfo=timezone.utc))


def test_a_payment_dated_just_ahead_of_now_is_not_recent():
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    order = {"status": "done", "paid_at": (now + timedelta(minutes=5)).isoformat(),
             "items_json": json.dumps([{"slug": PHYSICAL, "qty": 1}])}
    assert not tr.could_await_label(order, now=now)


@pytest.mark.parametrize("status", ["paid", "confirmed", "done"])
def test_an_added_order_that_had_its_parcel_does_not_steer_the_next_one(wcx, status):
    """Round 2: a tracked drop-ship must not send a later label to its practitioner."""
    oid = _with_dropship(wcx, client=None, status=status, **AT_LABEL)
    _set_paid(wcx, oid, 1)
    wcx.execute("UPDATE orders SET tracking_number='9400000000000000000009' WHERE id=?",
                (oid,))
    wcx.commit()
    r, sends, drafts = _real(wcx, _match("cyndi@gmail.example", "high"))
    assert all(to != PRAC for to, _h, _t in sends + drafts)


# --- a delivered scan on a linked done order -------------------------------------------

@pytest.fixture
def app_module():
    # No skip on failure: a broken import must fail this test, not hide it (round 2).
    import importlib
    return importlib.import_module("app")


def test_a_delivered_scan_moves_a_done_order_and_opens_its_month(app_module, monkeypatch):
    from dashboard import coaching as Co
    cx = _cx()
    Co.init_coaching_table(cx)
    oid = _order(cx, status="done")
    sid, res = _link(cx)
    assert res["order_ids"] == [oid]
    opened, biofield = [], []
    monkeypatch.setattr(app_module, "_extend_founding_membership_on_delivery",
                        lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_adjust_supply_on_delivery", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_open_coaching_for_order",
                        lambda cx, email, order_id, source, **k:
                        opened.append((email, order_id, k.get("started_at")))
                        or {"ok": True, "created": True, "ends_at": "2026-12-02"})
    monkeypatch.setattr(app_module, "_extend_biofield_month_on_delivery",
                        lambda cx, email, order: biofield.append(order["id"]))
    shipment = cx.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
    out = app_module._activate_coaching_for_shipment(cx, shipment,
                                                     delivered_at="2026-11-04T20:00:00Z")
    assert out["ok"] and out["opened"] == 1
    assert O.get_order(cx, oid)["status"] == "delivered"
    assert opened == [("cyndi@example.com", oid, "2026-11-04T20:00:00Z")]
    assert biofield == [oid]


# --- drop-ship tracking goes to the practitioner ----------------------------------------

def _set_paid(cx, oid, days_ago, slug=PHYSICAL):
    cx.execute("UPDATE orders SET paid_at=?, items_json=? WHERE id=?",
               (_days_ago(days_ago), json.dumps([{"slug": slug, "qty": 1}]), oid))
    cx.commit()


def _real(cx, find=lambda name: None, label=ONE_SHIPMENT):
    sends, drafts = [], []
    res = handle_confirmation(
        label, "m1", cx, find,
        lambda to, subject, html, text: drafts.append((to, html, text)) or "d1",
        send_fn=lambda to, subject, html, text: sends.append((to, html, text)) or "s1",
        auto_send=True, dry_run=False,
        address_resolve=lambda s: tr.resolve_by_address(cx, s))
    return res[0], sends, drafts


@pytest.fixture
def wcx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chat_log.db"))
    T.init_tracking_schema(conn)
    yield conn
    conn.close()


def test_the_order_270_shape_sends_to_the_practitioner_and_names_the_client(wcx):
    """A done drop-ship, client mail off, no client email typed."""
    oid = _with_dropship(wcx, client=None, status="done", **AT_LABEL)
    _set_paid(wcx, oid, 1)
    r, sends, drafts = _real(wcx, _match("cyndi@gmail.example", "high"))
    assert r["status"] == "sent" and drafts == []
    (to, html, text), = sends
    assert to == PRAC
    assert text.startswith("Aloha Ashley,\n\nThe order for Cyndi O'Brien is on its way. "
                           "Here is the USPS tracking number:")
    assert "Your order" not in text and "Aloha Cyndi" not in text
    assert "The order for Cyndi O&#x27;Brien is on its way." in html


def test_an_open_dropship_to_the_practitioner_also_names_the_client(wcx):
    _with_dropship(wcx, **AT_LABEL)
    r, sends, _ = _real(wcx, _match(CLIENT, "high"))
    (to, _html, text), = sends
    assert to == PRAC and "Aloha Ashley," in text
    assert "The order for Cyndi O'Brien is on its way." in text


def test_an_old_done_dropship_is_still_held_from_the_client(wcx):
    """Outside the grace period the board does not see the order, so the hold stands."""
    oid = _with_dropship(wcx, client=None, status="done", **AT_LABEL)
    _set_paid(wcx, oid, 40)
    r, sends, drafts = _real(wcx, _match("cyndi@gmail.example", "high"))
    assert sends == [] and r["confidence"] == "dropship-held"
    (to, _html, text), = drafts
    assert to is None and "Aloha Cyndi," in text and "The order for" not in text


def test_switch_on_the_client_gets_the_usual_wording(wcx):
    oid = _with_dropship(wcx, switch=True, status="done", **AT_LABEL)
    _set_paid(wcx, oid, 1)
    r, sends, _ = _real(wcx)
    (to, _html, text), = sends
    assert to == CLIENT and "Aloha Cyndi," in text
    assert "Your order is on its way. Here is your USPS tracking number:" in text


def test_two_boxes_to_the_practitioner_name_the_client_once():
    email = T.build_tracking_email(["9400000000000000000001", "9400000000000000000002"],
                                   "Ashley King", resolved_email=PRAC,
                                   for_client="Cyndi O'Brien")
    assert "Aloha Ashley," in email["text"]
    assert ("The order for Cyndi O'Brien is on its way. Here are the USPS tracking "
            "numbers, one for each box:") in email["text"]
    assert "Box 2: 9400000000000000000002" in email["text"]
    assert "your" not in email["text"].split("Aloha")[1].split("Box 1")[0].lower()


def test_no_practitioner_name_means_no_greeting_rather_than_the_clients():
    email = T.build_tracking_email("9400000000000000000001", None, resolved_email=PRAC,
                                   for_client="Cyndi O'Brien")
    assert email["text"].startswith("The order for Cyndi O'Brien is on its way.")
