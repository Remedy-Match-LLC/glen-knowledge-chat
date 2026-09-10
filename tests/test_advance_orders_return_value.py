"""_advance_orders_by_tracking_status must report what it MOVED, not what it tried.

Two callers count its return value. The USPS status sweep prints it as
`cards_reported` in the 15-minute cron line, and /api/cron/easypost-sync sums it
as `activated`. The delivered branch used to `return 1` unconditionally, including
when the shipment resolved to no member order at all.

That is the common case, not an edge one. Parcel ...869056 was delivered
2026-09-09, its shipment row exists, and no order on the board carries its
tracking number, because link_shipment_to_orders only links orders in new/packed
with no tracking number and order 170 was already 'shipped'. A flat 1 told Glen a
card had moved when the board had not changed.

The in_transit branch already counted correctly; only delivered lied.
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


TN = "9405530109355412869056"


def _stub_shipment(app_module, monkeypatch, row):
    monkeypatch.setattr(app_module._tracking, "shipment_by_tracking",
                        lambda cx, tn: row)


def test_delivered_with_no_member_order_reports_zero(app_module, monkeypatch):
    """The observed production case. delivered_at still gets written, but no card
    moved, so the count must be 0."""
    _stub_shipment(app_module, monkeypatch, {"id": 58, "order_uuid": None})
    monkeypatch.setattr(app_module, "_activate_coaching_for_shipment",
                        lambda cx, sh, *, delivered_at: {"ok": False,
                                                         "reason": "unresolved"})

    got = app_module._advance_orders_by_tracking_status(None, TN, "delivered")

    assert got == 0


def test_delivered_already_processed_reports_zero(app_module, monkeypatch):
    """A re-run must not re-report a card as moved. This is what makes the cron
    line readable across 96 runs a day."""
    _stub_shipment(app_module, monkeypatch, {"id": 58, "order_uuid": None})
    monkeypatch.setattr(app_module, "_activate_coaching_for_shipment",
                        lambda cx, sh, *, delivered_at: {"skipped":
                                                         "already_processed"})

    got = app_module._advance_orders_by_tracking_status(None, TN, "delivered")

    assert got == 0


def test_delivered_counts_every_member_of_a_household_shipment(
        app_module, monkeypatch):
    """A combined household shipment shares one tracking number across several
    member orders, and each one is set to delivered. All of them count."""
    _stub_shipment(app_module, monkeypatch, {"id": 58, "order_uuid": "u1"})
    monkeypatch.setattr(
        app_module, "_activate_coaching_for_shipment",
        lambda cx, sh, *, delivered_at: {
            "ok": True, "opened": 1,
            "members": [{"order_id": 11, "ok": True},
                        {"order_id": 12, "ok": False, "reason": "no_email"},
                        {"order_id": 13, "ok": True}]})

    got = app_module._advance_orders_by_tracking_status(None, TN, "delivered")

    # 3 orders were set to 'delivered' even though only 2 opened a window. The
    # count is about order cards, not coaching windows.
    assert got == 3


def test_an_unknown_tracking_number_reports_zero(app_module, monkeypatch):
    _stub_shipment(app_module, monkeypatch, None)
    assert app_module._advance_orders_by_tracking_status(
        None, TN, "delivered") == 0


@pytest.mark.parametrize("status", ["pre_transit", "unknown", "", None])
def test_a_status_we_do_not_act_on_reports_zero(app_module, monkeypatch, status):
    _stub_shipment(app_module, monkeypatch, {"id": 58, "order_uuid": None})
    assert app_module._advance_orders_by_tracking_status(None, TN, status) == 0


def test_a_malformed_activation_result_does_not_raise(app_module, monkeypatch):
    """A future change to _activate_coaching_for_shipment must degrade the count,
    never 500 the cron."""
    _stub_shipment(app_module, monkeypatch, {"id": 58, "order_uuid": None})
    for result in [None, {}, {"members": None}]:
        monkeypatch.setattr(app_module, "_activate_coaching_for_shipment",
                            lambda cx, sh, *, delivered_at, r=result: r)
        assert app_module._advance_orders_by_tracking_status(
            None, TN, "delivered") == 0
