"""The invoice email greets the client with a capitalised name.

2026-10-13: Linda Maher's invoice email said "Hi linda maher," because the order
stored her name as typed, all lowercase.
"""
import pytest

from tests.test_phase3_paylink import _orders_db, _capture_send_email


@pytest.mark.parametrize("stored,greeting", [
    ("linda maher", "Hi Linda Maher,"),
    ("SKYLAR VERCHES", "Hi Skylar Verches,"),
    ("DeAnna McDonald", "Hi DeAnna McDonald,"),       # mixed case is left as typed
    ("maria van der vegt", "Hi Maria van der Vegt,"),
    ("desiree&#39; dallaguardia", "Hi Desiree' Dallaguardia,"),
    ("", "Hi there,"),
])
def test_the_greeting_capitalises_the_stored_name(monkeypatch, stored, greeting):
    O, cx = _orders_db()
    monkeypatch.setenv("INVOICE_PAYLINK_ENABLED", "1")
    cap = _capture_send_email(monkeypatch)
    oid = O.upsert_order(cx, source="in-house", external_ref="INH-1", status="new",
                         total_cents=100, email="a@b.com", name=stored)
    O._send_invoice_exec({"order_id": oid}, {"cx": cx})
    assert cap["plain"].startswith(greeting)


def test_a_name_is_escaped_in_the_html(monkeypatch):
    O, cx = _orders_db()
    monkeypatch.setenv("INVOICE_PAYLINK_ENABLED", "1")
    cap = _capture_send_email(monkeypatch)
    oid = O.upsert_order(cx, source="in-house", external_ref="INH-2", status="new",
                         total_cents=100, email="a@b.com", name="<b>bob</b>")
    O._send_invoice_exec({"order_id": oid}, {"cx": cx})
    assert "<b>bob" not in cap["html"].lower() and "&lt;b&gt;" in cap["html"].lower()
