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


BLANK = {"street": "", "address2": "", "city": "", "state": "", "zip": "", "country": "US"}


def test_a_newer_order_with_a_blank_street_does_not_hide_the_real_one(env):
    """Newest-row shadowing: a later order saved with {"street": ""} is not empty
    JSON, so it used to win and the fallback found no address."""
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        O.upsert_order(cx, source="in-house", external_ref="LATER-blank", email="sharon@x.com",
                       name="Later", address=BLANK, items=[], total_cents=0, status="done")
        cx.execute("UPDATE orders SET created_at='2999-01-01' WHERE external_ref='LATER-blank'")
        cx.commit()
    a = _address(db, _post(appmod, "sharon@x.com"))
    assert a["street"] == "5844 E Enrose St"


def _seed_person(db, email, **addr):
    from dashboard import customers as C
    with sqlite3.connect(db) as cx:
        C.add_people_address_columns(cx)
        cx.execute("INSERT INTO people (name, email, address1, city, state, zip, country, "
                   "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   ("P", email, addr.get("address1", ""), addr.get("city", ""),
                    addr.get("state", ""), addr.get("zip", ""), addr.get("country", ""),
                    "2026-10-01"))
        cx.commit()


def test_with_no_earlier_order_the_people_record_address_is_used(env):
    appmod, db = env
    _seed_person(db, "new@x.com", address1="12 Kino'ole St", city="Hilo", state="HI",
                 zip="96720")
    a = _address(db, _post(appmod, "new@x.com"))
    assert (a["street"], a["city"], a["zip"]) == ("12 Kino'ole St", "Hilo", "96720")


def test_an_earlier_order_beats_the_people_record(env):
    appmod, db = env
    _seed_order(db, "sharon@x.com", MESA)
    _seed_person(db, "sharon@x.com", address1="1 Old Rd", city="Hilo", state="HI",
                 zip="96720")
    assert _address(db, _post(appmod, "sharon@x.com"))["street"] == "5844 E Enrose St"


def test_a_pet_uses_the_caregivers_people_record(env):
    appmod, db = env
    _seed_person(db, "carer@x.com", address1="12 Kino'ole St", city="Hilo", state="HI",
                 zip="96720")
    from dashboard import household as H
    with sqlite3.connect(db) as cx:
        H.init_household_tables(cx)
        H.add_member(cx, "carer@x.com", "pet@x.com", "Rex", "pet")
        cx.commit()
    assert _address(db, _post(appmod, "pet@x.com"))["street"] == "12 Kino'ole St"


def _seed_fmp(db, client_id, email, *addresses):
    from dashboard import fmp_orders as F
    with sqlite3.connect(db) as cx:
        F.ensure_tables(cx)
        cx.execute("INSERT INTO fmp_clients (id_pk, name_first, name_last, email) "
                   "VALUES (?,?,?,?)", (client_id, "F", "M", email))
        for i, (street, city, prov, postal, country) in enumerate(addresses):
            cx.execute("INSERT INTO fmp_client_addresses (id_pk, id_fk_client, street, city, "
                       "province, postal_code, country) VALUES (?,?,?,?,?,?,?)",
                       (f"{client_id}-{i}", client_id, street, city, prov, postal, country))
        cx.commit()


HILO = ("12 Kino'ole St", "Hilo", "HI", "96720", "USA")


def test_with_no_order_or_people_address_filemaker_is_used(env):
    appmod, db = env
    _seed_fmp(db, "c1", "fm@x.com", HILO)
    a = _address(db, _post(appmod, "fm@x.com"))
    assert (a["street"], a["city"], a["state"], a["zip"], a["country"]) == (
        "12 Kino'ole St", "Hilo", "HI", "96720", "US")


def test_filemaker_with_two_different_streets_leaves_it_blank(env):
    """Glen, 2026-10-03: same street only."""
    appmod, db = env
    _seed_fmp(db, "c1", "fm@x.com", HILO, ("9 Other Rd", "Pahoa", "HI", "96778", "USA"))
    assert _address(db, _post(appmod, "fm@x.com")).get("street", "") == ""


def test_two_filemaker_clients_on_one_email_must_agree(env):
    appmod, db = env
    _seed_fmp(db, "c1", "fm@x.com", HILO)
    _seed_fmp(db, "c2", "FM@x.com", ("9 Other Rd", "Pahoa", "HI", "96778", "USA"))
    assert _address(db, _post(appmod, "fm@x.com")).get("street", "") == ""


def test_the_same_street_on_two_records_counts_as_one(env):
    appmod, db = env
    _seed_fmp(db, "c1", "fm@x.com", HILO, ("12 Kino'ole St.", "Hilo", "HI", "96720", "USA"))
    assert _address(db, _post(appmod, "fm@x.com"))["street"] == "12 Kino'ole St"


def test_an_unknown_country_leaves_it_blank(env):
    appmod, db = env
    _seed_fmp(db, "c1", "fm@x.com", ("1 Rue X", "Brussels", "", "1000", "BELGIUM"))
    assert _address(db, _post(appmod, "fm@x.com")).get("street", "") == ""


def test_the_people_record_beats_filemaker(env):
    appmod, db = env
    _seed_person(db, "fm@x.com", address1="5 People Pl", city="Hilo", state="HI", zip="96720")
    _seed_fmp(db, "c1", "fm@x.com", HILO)
    assert _address(db, _post(appmod, "fm@x.com"))["street"] == "5 People Pl"


def test_a_non_us_filemaker_address_leaves_it_blank_and_the_hand_off_succeeds(env):
    """Round 1: a Canadian fill made the post fail with "We ship to US addresses only"."""
    appmod, db = env
    _seed_fmp(db, "c1", "ca@x.com", ("1 Bay St", "Toronto", "ON", "M5J 2N8", "CANADA"))
    assert _address(db, _post(appmod, "ca@x.com")).get("street", "") == ""


def test_spelled_out_country_and_state_are_normalised(env):
    """Round 1: "United States" blocked the post; "Hawaii" dropped GET, charged only on HI."""
    appmod, db = env
    _seed_person(db, "p@x.com", address1="12 Kino'ole St", city="Hilo", state="Hawaii",
                 zip="96720", country="United States")
    a = _address(db, _post(appmod, "p@x.com"))
    assert (a["street"], a["state"], a["country"]) == ("12 Kino'ole St", "HI", "US")


def test_an_incomplete_earlier_order_is_not_replaced_by_filemaker(env):
    appmod, db = env
    _seed_order(db, "inc@x.com", {"street": "12 K St", "city": "", "state": "", "zip": ""})
    _seed_fmp(db, "c1", "inc@x.com", HILO)
    assert _address(db, _post(appmod, "inc@x.com")).get("street", "") == ""


def test_the_okina_spelling_is_hi(env):
    appmod, db = env
    _seed_person(db, "ok@x.com", address1="12 Kino'ole St", city="Hilo", state="Hawaiʻi",
                 zip="96720")
    assert _address(db, _post(appmod, "ok@x.com"))["state"] == "HI"


def test_filemaker_kingdom_of_hawaii_with_no_province_is_hi(env):
    appmod, db = env
    _seed_fmp(db, "c1", "k@x.com", ("12 Kino'ole St", "Hilo", "", "96720", "Kingdom of Hawai'i"))
    a = _address(db, _post(appmod, "k@x.com"))
    assert (a["state"], a["country"]) == ("HI", "US")


def test_a_newer_order_that_cannot_ship_leaves_it_blank(env):
    """Round 3: skipping the newest order for an older one ships to where the client
    used to live. Blank goes to Rae instead. (Round 2 had chosen the older order.)"""
    appmod, db = env
    _seed_order(db, "o@x.com", MESA)
    from dashboard import orders as O
    with sqlite3.connect(db) as cx:
        O.upsert_order(cx, source="in-house", external_ref="LATER-inc", email="o@x.com",
                       name="Later", address={"street": "12 K St"}, items=[], total_cents=0,
                       status="done")
        cx.execute("UPDATE orders SET created_at='2999-01-01' WHERE external_ref='LATER-inc'")
        cx.commit()
    _seed_person(db, "o@x.com", address1="1 Old Rd", city="Hilo", state="HI", zip="96720")
    assert _address(db, _post(appmod, "o@x.com")).get("street", "") == ""


def test_a_blank_province_copy_does_not_hide_an_agreeing_complete_one(env):
    appmod, db = env
    _seed_fmp(db, "c1", "dup@x.com", ("12 Kino'ole St", "Hilo", "", "96720", "USA"), HILO)
    assert _address(db, _post(appmod, "dup@x.com"))["state"] == "HI"


def test_a_whitespace_only_filemaker_street_is_ignored_not_a_crash(env):
    appmod, db = env
    _seed_fmp(db, "c1", "ws@x.com", ("\t\n", "Hilo", "HI", "96720", "USA"))
    assert _address(db, _post(appmod, "ws@x.com")).get("street", "") == ""


def test_a_test_street_is_never_filled(env):
    """Fulfillment review 2026-10-03: order 173 would have got "1 Test, Hilo"."""
    appmod, db = env
    _seed_order(db, "t@x.com", {"street": "1 Test", "city": "Hilo", "state": "HI",
                                "zip": "96720", "country": "US"})
    assert _address(db, _post(appmod, "t@x.com")).get("street", "") == ""


def test_a_street_containing_test_as_part_of_a_word_still_fills(env):
    appmod, db = env
    _seed_order(db, "t@x.com", {"street": "12 Testa Ln", "city": "Hilo", "state": "HI",
                                "zip": "96720", "country": "US"})
    assert _address(db, _post(appmod, "t@x.com"))["street"] == "12 Testa Ln"


def test_glens_own_email_is_never_filled(env):
    appmod, db = env
    _seed_order(db, "drglenswartwout@gmail.com", MESA)
    assert _address(db, _post(appmod, "drglenswartwout@gmail.com")).get("street", "") == ""
