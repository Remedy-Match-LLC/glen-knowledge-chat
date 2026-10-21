"""A tracking email's recipient is found by SHIP-TO ADDRESS before any name-only guess.

Glen, 2026-09-22 (relayed by fulfillment): tracking emails send as soon as the number
exists; a partial name match auto-sends only when the ship-to address also matches; and
recipients "are likely somewhere accessible to be matched". The watcher matched on NAME
only (GHL, then order-email harvest). These cases are the watcher's last three drafts,
measured against the 2026-09-13 FileMaker export, with the addresses made up.
"""
import json
import sqlite3

import pytest

from dashboard import tracking_recipient as tr


def _db():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, name TEXT, "
               "status TEXT, address_json TEXT)")
    cx.execute("CREATE TABLE fmp_clients (id_pk TEXT, name_first TEXT, name_last TEXT, "
               "company TEXT, email TEXT, phone_res TEXT, phone_cell TEXT, "
               "phone_business TEXT)")
    cx.execute("CREATE TABLE fmp_client_addresses (id_pk TEXT, id_fk_client TEXT, type TEXT, "
               "street TEXT, city TEXT, province TEXT, postal_code TEXT, country TEXT)")
    return cx


def _fmp(cx, cid, first, last, email, street, zip5):
    cx.execute("INSERT INTO fmp_clients (id_pk, name_first, name_last, email) VALUES (?,?,?,?)",
               (cid, first, last, email))
    cx.execute("INSERT INTO fmp_client_addresses (id_pk, id_fk_client, street, postal_code) "
               "VALUES (?,?,?,?)", ("a" + cid, cid, street, zip5))


def _order(cx, email, name, street, zip5, status="packed"):
    cx.execute("INSERT INTO orders (email, name, status, address_json) VALUES (?,?,?,?)",
               (email, name, status, json.dumps({"street": street, "zip": zip5})))


def _ship(name, street, zip5):
    return {"recipient_name": name, "street": street, "city": "X", "state": "CA", "zip": zip5}


# --- the three measured cases ---------------------------------------------------------

def test_a_hyphenated_surname_agrees_at_the_right_address():
    """Robin Roemer Brown on the label, Robin Roemer-Brown in FileMaker."""
    cx = _db()
    _fmp(cx, "1", "Robin", "Roemer-Brown", "tammara@example.com", "12 Ocean View Drive", "90275")
    r = tr.resolve_by_address(cx, _ship("Robin Roemer Brown", "12 Ocean View Dr", "90275-1234"))
    assert (r["email"], r["source"]) == ("tammara@example.com", "fmp")


def test_a_short_first_name_agrees_and_the_household_member_does_not():
    """Judy Tom on the label; Judith Tom and Robert Tom share the address."""
    cx = _db()
    _fmp(cx, "1", "Judith", "Tom", "judith@example.com", "5 Kahala Ave", "96822")
    _fmp(cx, "2", "Robert", "Tom", "robert@example.com", "5 Kahala Avenue", "96822")
    r = tr.resolve_by_address(cx, _ship("Judy Tom", "5 Kahala Ave", "96822"))
    assert (r["email"], r["source"]) == ("judith@example.com", "fmp")


def test_a_trailing_apostrophe_in_the_export_still_agrees():
    cx = _db()
    _fmp(cx, "1", "Desiree'", "Dalla Guardia", "desiree@example.com", "9 Pine St", "81005")
    r = tr.resolve_by_address(cx, _ship("Desiree Dalla Guardia", "9 Pine Street", "81005"))
    assert r["email"] == "desiree@example.com"


# --- order of standing ------------------------------------------------------------------

def test_the_board_order_wins_over_filemaker():
    """The board email is what the customer typed for this purchase; FileMaker is older."""
    cx = _db()
    _fmp(cx, "1", "Ann", "Lee", "old@example.com", "1 Main St", "10001")
    _order(cx, "new@example.com", "Ann Lee", "1 Main Street", "10001")
    r = tr.resolve_by_address(cx, _ship("Ann Lee", "1 Main St", "10001"))
    assert (r["email"], r["source"]) == ("new@example.com", "board")


def test_a_closed_or_cancelled_board_order_is_not_this_parcel():
    cx = _db()
    _order(cx, "gone@example.com", "Ann Lee", "1 Main St", "10001", status="cancelled")
    r = tr.resolve_by_address(cx, _ship("Ann Lee", "1 Main St", "10001"))
    assert r["email"] is None


# --- refusals ----------------------------------------------------------------------------

def test_two_agreeing_clients_at_one_address_are_ambiguous():
    cx = _db()
    _fmp(cx, "1", "Jo", "Kim", "jo1@example.com", "3 Elm Rd", "20001")
    _fmp(cx, "2", "Joanne", "Kim", "jo2@example.com", "3 Elm Road", "20001")
    r = tr.resolve_by_address(cx, _ship("J Kim", "3 Elm Rd", "20001"))
    assert r["email"] is None and r["conflict"] is True


def test_a_different_surname_never_agrees():
    """A married-name change goes to Rae, not to the old record."""
    cx = _db()
    _fmp(cx, "1", "Mary", "Smith", "mary@example.com", "7 Oak Ln", "30301")
    r = tr.resolve_by_address(cx, _ship("Mary Jones", "7 Oak Lane", "30301"))
    assert r["email"] is None and r["conflict"] is False


def test_the_right_name_at_a_different_address_is_not_a_match():
    cx = _db()
    _fmp(cx, "1", "Ann", "Lee", "ann@example.com", "1 Main St", "10001")
    r = tr.resolve_by_address(cx, _ship("Ann Lee", "2 Main St", "10001"))
    assert r["email"] is None


def test_missing_tables_resolve_to_nothing_rather_than_raising():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    r = tr.resolve_by_address(cx, _ship("Ann Lee", "1 Main St", "10001"))
    assert r["email"] is None


# --- the name rule ------------------------------------------------------------------------

@pytest.mark.parametrize("label,first,last,ok", [
    ("Robin Roemer Brown", "Robin", "Roemer-Brown", True),
    ("Judy Tom", "Judith", "Tom", True),
    ("Bill Ng", "William", "Ng", True),
    ("J Tom", "Judith", "Tom", True),
    ("Robert Tom", "Judith", "Tom", False),
    ("Mary Jones", "Mary", "Smith", False),
    ("Mary O'Neil", "Mary", "ONeil", True),
])
def test_names_agree(label, first, last, ok):
    assert tr.names_agree(label, first, last) is ok


# --- the Postgres cursor ------------------------------------------------------------------
# Production's cursor wrapper (dashboard.db._PgCursor) returns HybridRow objects and has
# NO `description`. Reading it on an empty result raised AttributeError in production,
# so the address step failed every time and the watcher fell back to name-only (#1790).

class _PgLikeCursor:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows            # deliberately no .description attribute


class _PgLikeConn:
    """Answers each query with rows built by dashboard.pgcompat.HybridRow, as prod does."""
    def __init__(self, cx):
        self._cx = cx
    def execute(self, sql, params=()):
        from dashboard.pgcompat import HybridRow
        cur = self._cx.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return _PgLikeCursor([HybridRow(cols, tuple(r)) for r in cur.fetchall()])
    def rollback(self):
        pass


def test_an_empty_result_on_the_postgres_cursor_does_not_raise():
    cx = _db()
    cx.row_factory = None
    r = tr.resolve_by_address(_PgLikeConn(cx), _ship("Ann Lee", "1 Main St", "10001"))
    assert r["email"] is None and r["reason"].startswith("no board order")


def test_filemaker_resolves_through_the_postgres_cursor():
    cx = _db()
    cx.row_factory = None
    _fmp(cx, "1", "Judith", "Tom", "judith@example.com", "5 Kahala Ave", "96822")
    r = tr.resolve_by_address(_PgLikeConn(cx), _ship("Judy Tom", "5 Kahala Ave", "96822"))
    assert (r["email"], r["source"]) == ("judith@example.com", "fmp")


# --- drop-ship clients ------------------------------------------------------------------
# Glen, 2026-10-21: a drop-ship practitioner's clients get mail from us only when that
# practitioner's switch (practitioner_settings.client_review_emails) is on. Off, the
# tracking goes to the practitioner, who is orders.email on a drop-ship.

def _dropship_db():
    from dashboard import orders as _orders
    from dashboard import practitioner_settings as _ps
    cx = _db()
    cx.execute("DROP TABLE orders")
    _orders.init_orders_table(cx)
    _ps.init_settings_table(cx)
    return cx


def _dropship(cx, street, zip5, *, pid="p1", prac="ashley@prac.example",
              client="tony@client.example", status="packed", ref="d1"):
    cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, status, "
               "practitioner_id, recipient_email, address_json) VALUES (?,?,?,?,?,?,?,?,?)",
               ("2026-10-01", "dropship", ref, prac, "Ashley King", status, pid, client,
                json.dumps({"name": "Tony Client", "street": street, "zip": zip5})))


def _own_order(cx, email, name, street, zip5, ref="s1"):
    cx.execute("INSERT INTO orders (created_at, source, external_ref, email, name, status, "
               "address_json) VALUES (?,?,?,?,?,?,?)",
               ("2026-10-01", "storefront", ref, email, name, "packed",
                json.dumps({"street": street, "zip": zip5})))


def test_a_dropship_order_at_the_address_wins_over_the_clients_own_order():
    """The client's own open order used to win by name; the practitioner's order now wins."""
    cx = _dropship_db()
    _dropship(cx, "4 Hale St", "96720")
    _own_order(cx, "tony@client.example", "Tony Client", "4 Hale Street", "96720")
    r = tr.resolve_by_address(cx, _ship("Tony Client", "4 Hale St", "96720"))
    assert (r["email"], r["source"]) == ("ashley@prac.example", "board")


def test_a_dropship_with_the_switch_on_resolves_to_the_client():
    from dashboard import practitioner_settings as _ps
    cx = _dropship_db()
    _dropship(cx, "4 Hale St", "96720")
    _ps.set_client_review_emails(cx, "p1", True)
    r = tr.resolve_by_address(cx, _ship("Tony Client", "4 Hale St", "96720"))
    assert r["email"] == "tony@client.example"


def test_held_recipients_follow_the_switch():
    from dashboard import practitioner_settings as _ps
    cx = _dropship_db()
    _dropship(cx, "4 Hale St", "96720", status="done")
    assert tr.dropship_recipients_held(cx) == {"tony@client.example": "ashley@prac.example"}
    _ps.set_client_review_emails(cx, "p1", True)
    assert tr.dropship_recipients_held(cx) == {}


def test_held_recipients_read_failure_is_none_not_empty():
    """An empty answer would let the watcher send; None makes it draft everything."""
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("db down")
        def rollback(self):
            pass
    assert tr.dropship_recipients_held(_Broken()) is None
