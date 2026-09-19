"""One email to a household payer with every invoice link they may pay (Glen, 2026-09-19).
Steve Fox pays for himself and Michael Hill."""
import sqlite3

import pytest

from dashboard import household as hh
from dashboard import orders as O
from dashboard import payer_links as pl

BASE = "https://illtowell.com"


@pytest.fixture
def cx(monkeypatch):
    monkeypatch.setenv("PAYER_LINKS_EMAIL_ENABLED", "1")
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    hh.init_household_tables(c)
    c.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, first_name TEXT)")
    c.execute("INSERT INTO people (email, first_name) VALUES ('steve@x.com','Steve')")
    return c


def _order(cx, email, name, total, group=None, sent=True, pay="unpaid", status="proposed"):
    oid = O.upsert_order(cx, source="in-house", external_ref="R-" + email + str(total),
                         email=email, name=name, items=[], total_cents=total, status=status)
    cx.execute("UPDATE orders SET group_shipment_id=?, pay_status=?, invoice_token=?, "
               "invoice_sent_at=? WHERE id=?",
               (group, pay, "tok" + str(oid) if sent else None,
                "2026-09-19T00:00:00" if sent else None, oid))
    cx.commit()
    return oid


def _send(cx, oid):
    outbox = []
    sent = pl.send_for(cx, O.get_order(cx, oid),
                       lambda to, subj, plain, **kw: outbox.append((to, subj, plain, kw["html"])),
                       BASE)
    return sent, outbox


def _steve_pays_for_michael(cx):
    hh.add_member(cx, "steve@x.com", "michael@x.com", "Michael Hill", "partner")
    hh.set_pay_consent(cx, "steve@x.com", "michael@x.com", 1)


def test_the_last_invoice_sends_one_email_with_both_links(cx):
    _steve_pays_for_michael(cx)
    a = _order(cx, "steve@x.com", "Steve Fox", 68338, group=9)
    b = _order(cx, "michael@x.com", "Michael Hill", 88862, group=9)
    sent, outbox = _send(cx, b)
    assert sent == [{"payer": "steve@x.com", "order_ids": [a, b]}]
    (to, subj, plain, html), = outbox
    assert to == "steve@x.com" and subj == "Your household invoices"
    assert plain.startswith("Aloha Steve,")
    assert f"Steve Fox, $683.38: {BASE}/invoice/tok{a}" in plain
    assert f"Michael Hill, $888.62: {BASE}/invoice/tok{b}" in plain
    assert "View and pay Michael's invoice" in html and "arial black" in html


def test_nothing_sends_until_every_invoice_in_the_group_is_sent(cx):
    _steve_pays_for_michael(cx)
    a = _order(cx, "steve@x.com", "Steve Fox", 100, group=9)
    _order(cx, "michael@x.com", "Michael Hill", 200, group=9, sent=False)
    assert _send(cx, a) == ([], [])


def test_it_never_sends_the_same_set_twice(cx):
    _steve_pays_for_michael(cx)
    _order(cx, "steve@x.com", "Steve Fox", 100, group=9)
    b = _order(cx, "michael@x.com", "Michael Hill", 200, group=9)
    assert len(_send(cx, b)[1]) == 1
    assert _send(cx, b) == ([], [])


def test_no_consent_no_email(cx):
    hh.add_member(cx, "steve@x.com", "michael@x.com", "Michael Hill", "partner")
    _order(cx, "steve@x.com", "Steve Fox", 100, group=9)
    b = _order(cx, "michael@x.com", "Michael Hill", 200, group=9)
    assert _send(cx, b) == ([], [])


def test_a_pet_needs_no_consent_and_a_lone_order_is_its_own_group(cx):
    hh.add_member(cx, "sharon@x.com", "hershey@x.com", "Hershey", "pet")
    h = _order(cx, "hershey@x.com", "Hershey Connour", 20288)
    sent, outbox = _send(cx, h)
    assert sent == [{"payer": "sharon@x.com", "order_ids": [h]}]
    assert outbox[0][1] == "Your household invoice"
    assert outbox[0][2].startswith("Aloha,")


def test_paid_and_cancelled_orders_are_left_out(cx):
    _steve_pays_for_michael(cx)
    _order(cx, "steve@x.com", "Steve Fox", 100, group=9, pay="paid")
    _order(cx, "michael@x.com", "Old", 50, group=9, sent=False, status="cancelled")
    b = _order(cx, "michael@x.com", "Michael Hill", 200, group=9)
    sent, _ = _send(cx, b)
    assert sent == [{"payer": "steve@x.com", "order_ids": [b]}]


def test_off_by_default(cx, monkeypatch):
    monkeypatch.delenv("PAYER_LINKS_EMAIL_ENABLED")
    _steve_pays_for_michael(cx)
    b = _order(cx, "michael@x.com", "Michael Hill", 200, group=9)
    assert _send(cx, b) == ([], [])
