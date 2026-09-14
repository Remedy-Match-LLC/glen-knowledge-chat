"""A parcel marked delivered before its order was linked must not strand the order.

`_activate_coaching_for_shipment` used to return early on `delivered_at` alone. So
the first delivery signal consumed the parcel: it stamped delivered_at, found no
member orders, and returned. Any order linked afterwards could never advance, and
every later sweep answered `already_processed`.

That is not hypothetical. On 2026-09-11 the link filter was widened so orders 146
(Pamela Kilmer) and 170 (Stephanie Greenwood) finally picked up their parcels — but
both parcels had already been swept hours earlier while unlinked. Both orders sat at
`shipped`, with no coaching month, after USPS had reported them delivered.

Falling through is safe because every write below the guard is independently
idempotent. These tests pin both halves: the retry works, and it does not
double-grant.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app_module():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")


@pytest.fixture
def cx():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE shipments (id INTEGER, delivered_at TEXT, "
                 "coaching_opened INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO shipments VALUES (19, NULL, 0)")
    conn.commit()
    yield conn
    conn.close()


def _wire(app_module, monkeypatch, members, opened_calls):
    monkeypatch.setattr(app_module._tracking, "mark_shipment_delivered",
                        lambda cx, sid, at: True)
    monkeypatch.setattr(app_module._coaching, "shipment_member_orders",
                        lambda cx, sid, uuid: members)
    moved = []
    monkeypatch.setattr(app_module._bos_orders, "set_order_status",
                        lambda cx, oid, st: moved.append((oid, st)) or True)
    monkeypatch.setattr(app_module._bos_orders, "get_order", lambda cx, oid: {})
    monkeypatch.setattr(app_module, "_extend_biofield_month_on_delivery",
                        lambda *a, **k: "none")

    def opener(cx, email, order_id, source, window_source="self_serve",
               started_at=None):
        opened_calls.append({"order_id": order_id, "started_at": started_at})
        return {"ok": True, "created": True, "ends_at": "2026-09-30T00:00:00Z"}
    monkeypatch.setattr(app_module, "_open_coaching_for_order", opener)
    return moved


MEMBER = [{"id": 146, "email": "pk@example.com", "source": "groovekart"}]


def test_a_parcel_seen_while_unlinked_still_advances_the_order_later(
        app_module, monkeypatch, cx):
    """The exact case. delivered_at was stamped on the first sweep, when the order
    was not yet linked, so no coaching was opened."""
    cx.execute("UPDATE shipments SET delivered_at='2026-08-31T10:25:00Z', "
               "coaching_opened=0 WHERE id=19")
    cx.commit()
    opened = []
    moved = _wire(app_module, monkeypatch, MEMBER, opened)

    shipment = {"id": 19, "order_uuid": None,
                "delivered_at": "2026-08-31T10:25:00Z", "coaching_opened": 0,
                "resolved_email": "pk@example.com"}
    out = app_module._activate_coaching_for_shipment(
        cx, shipment, delivered_at="2026-08-31T10:25:00Z")

    assert out.get("skipped") != "already_processed"
    assert moved == [(146, "delivered")]
    assert len(opened) == 1
    # And the window is still dated from the real delivery, not from the retry.
    assert opened[0]["started_at"] == "2026-08-31T10:25:00Z"


def test_a_parcel_whose_coaching_already_opened_is_skipped(
        app_module, monkeypatch, cx):
    """Idempotence must survive the narrowing, or a re-sweep grants a second month."""
    opened = []
    _wire(app_module, monkeypatch, MEMBER, opened)

    shipment = {"id": 19, "order_uuid": None,
                "delivered_at": "2026-08-31T10:25:00Z", "coaching_opened": 1,
                "resolved_email": "pk@example.com"}
    out = app_module._activate_coaching_for_shipment(
        cx, shipment, delivered_at="2026-08-31T10:25:00Z")

    assert out == {"skipped": "already_processed"}
    assert opened == []


def test_a_fresh_parcel_still_works(app_module, monkeypatch, cx):
    opened = []
    moved = _wire(app_module, monkeypatch, MEMBER, opened)
    shipment = {"id": 19, "order_uuid": None, "delivered_at": None,
                "coaching_opened": 0, "resolved_email": "pk@example.com"}

    app_module._activate_coaching_for_shipment(
        cx, shipment, delivered_at="2026-09-11T12:00:00Z")

    assert moved == [(146, "delivered")]
    assert len(opened) == 1


def test_a_parcel_with_no_orders_is_retried_but_changes_nothing(
        app_module, monkeypatch, cx):
    """Rae's FMP parcels have no console order and never will. Retrying them each
    sweep is a no-op, which is the acceptable cost of not stranding real ones."""
    opened = []
    _wire(app_module, monkeypatch, [], opened)
    shipment = {"id": 19, "order_uuid": None,
                "delivered_at": "2026-08-31T10:25:00Z", "coaching_opened": 0,
                "resolved_email": ""}

    out = app_module._activate_coaching_for_shipment(
        cx, shipment, delivered_at="2026-08-31T10:25:00Z")

    assert out == {"ok": False, "reason": "unresolved"}
    assert opened == []


def test_a_missing_coaching_opened_column_does_not_block_the_retry(
        app_module, monkeypatch, cx):
    """Older shipment rows predate the column. _shipment_field returns None, which
    must read as 'not opened' rather than raising."""
    opened = []
    moved = _wire(app_module, monkeypatch, MEMBER, opened)
    shipment = {"id": 19, "order_uuid": None,
                "delivered_at": "2026-08-31T10:25:00Z",
                "resolved_email": "pk@example.com"}          # no coaching_opened

    app_module._activate_coaching_for_shipment(
        cx, shipment, delivered_at="2026-08-31T10:25:00Z")

    assert moved == [(146, "delivered")]
    assert len(opened) == 1
