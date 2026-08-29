import sqlite3

from dashboard import order_payments, orders
from dashboard.zelle_email_import import _matching_order, parse_subject


def _db():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    orders.init_orders_table(cx)
    order_payments.ensure_table(cx)
    return cx


def test_parse_bofa_subject():
    assert parse_subject("Krist Symons sent you $163.00") == {
        "payer": "Krist Symons", "amount_cents": 16300}
    assert parse_subject("A payment was sent") is None


def test_unique_household_name_and_exact_balance_matches():
    cx = _db()
    oid = orders.upsert_order(cx, source="in-house", external_ref="R1",
                              name="Russell Symons", total_cents=17767,
                              status="proposed")
    order_payments.add_payment(cx, oid, 1467, "Zelle", external_ref="old")
    match, reason = _matching_order(cx, "Krist Symons", 16300)
    assert reason is None and match["id"] == oid


def test_amount_only_never_matches_and_ambiguity_never_guesses():
    cx = _db()
    orders.upsert_order(cx, source="in-house", external_ref="A", name="Alex Other",
                        total_cents=16300, status="proposed")
    match, reason = _matching_order(cx, "Krist Symons", 16300)
    assert match is None and reason == "no exact open-balance match"
    orders.upsert_order(cx, source="in-house", external_ref="S1", name="Russell Symons",
                        total_cents=16300, status="proposed")
    orders.upsert_order(cx, source="in-house", external_ref="S2", name="Robin Symons",
                        total_cents=16300, status="confirmed")
    match, reason = _matching_order(cx, "Krist Symons", 16300)
    assert match is None and reason == "multiple exact open-balance matches"
