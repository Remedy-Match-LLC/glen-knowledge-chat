"""A hand-off invoice posts no address, so every Biofield invoice was created with a
blank ship-to. A blank ship-to never matches another order, so the Orders board never
offered Combine (Sharon and Hershey Connour, 2026-09-19), and the order could not ship.

Rule: a posted street always wins. With none, and not a pickup, use the client's last
shipped-to address. With none of their own, a pet or child uses their caregiver's.
"""
import json
import sqlite3

import pytest

from tests.test_orders_manual_pickup_pref import env  # noqa: F401  (fixture)

MESA = {"street": "5844 E Enrose St", "address2": "", "city": "Mesa", "state": "AZ",
        "zip": "85205", "country": "US"}


def _seed_order(db, email, address):
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        O.upsert_order(cx, source="in-house", external_ref="PRIOR-" + email, email=email,
                       name="Prior", address=address, items=[], total_cents=0,
                       status="done")


def _post(appmod, email, address=None, pickup=None):
    cust = {"name": "C", "email": email}
    if address is not None:
        cust["address"] = address
    body = {"customer": cust, "lines": [{"slug": "mix", "qty": 1}]}
    if pickup is not None:
        body["pickup"] = pickup
    r = appmod.app.test_client().post("/api/orders/manual", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    oid = r.get_json()["order_id"]
    return oid


def _address(db, oid):
    with sqlite3.connect(db) as cx:
        return json.loads(cx.execute("SELECT address_json FROM orders WHERE id=?",
                                     (oid,)).fetchone()[0] or "{}")


def test_blank_address_uses_the_clients_last_shipped_address(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    a = _address(db, _post(appmod, "sharon@x.com"))
    assert (a["street"], a["zip"], a["city"]) == ("5844 E Enrose St", "85205", "Mesa")


def test_a_posted_street_always_wins(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    a = _address(db, _post(appmod, "sharon@x.com",
                           address={"address1": "1 Other Rd", "zip": "96720"}))
    assert a["street"] == "1 Other Rd"


def test_a_pet_with_no_address_uses_the_caregivers(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    from dashboard import household as H
    with sqlite3.connect(db) as cx:
        H.init_household_tables(cx)
        H.add_member(cx, "sharon@x.com", "hershey@x.com", "Hershey", "pet")
        cx.commit()
    a = _address(db, _post(appmod, "hershey@x.com"))
    assert a["street"] == "5844 E Enrose St"


def test_an_adult_member_does_not_borrow_the_primarys_address(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    from dashboard import household as H
    with sqlite3.connect(db) as cx:
        H.init_household_tables(cx)
        H.add_member(cx, "sharon@x.com", "spouse@x.com", "Sam", "spouse")
        cx.commit()
    assert _address(db, _post(appmod, "spouse@x.com")).get("street", "") == ""


def test_a_pickup_is_not_given_an_address(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    assert _address(db, _post(appmod, "sharon@x.com", pickup=True)).get("street", "") == ""
