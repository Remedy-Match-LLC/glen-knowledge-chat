"""Created, opened and signed-in are three different questions.

Only the second and third say whether the portal works as a product. A portal is created
FOR a client by the practice; opening it is something the client chooses to do.

The trap this guards against is the one that inflated the affiliate count earlier the
same day: counting different kinds of thing and presenting the total as people.
"""
import sqlite3
import pytest
from dashboard import portal_usage as pu

DDL = """
CREATE TABLE client_portals (id INTEGER PRIMARY KEY, token_hash TEXT, email TEXT,
  name TEXT, content_json TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE portal_opens (id INTEGER PRIMARY KEY, kind TEXT, key TEXT,
  first_opened TEXT, last_opened TEXT, open_count INTEGER);
CREATE TABLE portal_credentials (person_id INTEGER PRIMARY KEY, password_hash TEXT,
  password_set_at TEXT, failed_attempts INTEGER);
CREATE TABLE portal_auth_events (event_id TEXT PRIMARY KEY, person_id INTEGER,
  email_hash TEXT, event TEXT, provider TEXT, created_at TEXT);
"""


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.executescript(DDL)
    return c


def test_portals_are_counted_by_row_and_by_person(cx):
    """One client can have more than one portal row. Both numbers are useful and they
    are not the same number."""
    cx.execute("INSERT INTO client_portals (email) VALUES ('a@x.com')")
    cx.execute("INSERT INTO client_portals (email) VALUES ('a@x.com')")
    cx.execute("INSERT INTO client_portals (email) VALUES ('b@x.com')")
    cx.commit()
    s = pu.summary(cx)
    assert s["portals_created"] == 3
    assert s["distinct_clients_with_a_portal"] == 2


def test_opens_are_reported_per_kind_not_summed_into_people(cx):
    cx.execute("INSERT INTO portal_opens (kind,key,open_count) VALUES ('report','r1',4)")
    cx.execute("INSERT INTO portal_opens (kind,key,open_count) VALUES ('report','r2',1)")
    cx.execute("INSERT INTO portal_opens (kind,key,open_count) VALUES ('invoice','i1',2)")
    cx.commit()
    s = pu.summary(cx)
    assert s["opens_by_kind"] == {"report": 2, "invoice": 1}
    assert s["distinct_things_opened"] == 3
    assert s["total_open_events"] == 7, "repeat opens are visits, not people"


def test_signing_in_is_counted_apart_from_opening(cx):
    """Setting a password and returning is a much stronger signal than following a link
    once, so it must not be blended into an opens number."""
    cx.execute("INSERT INTO portal_credentials VALUES (1,'hash','2026-01-01',0)")
    cx.execute("INSERT INTO portal_credentials VALUES (2,'','2026-01-01',0)")
    cx.execute("INSERT INTO portal_auth_events VALUES ('e1',1,'h','login','password','t')")
    cx.execute("INSERT INTO portal_auth_events VALUES ('e2',1,'h','login','password','t')")
    cx.commit()
    s = pu.summary(cx)
    assert s["set_a_password"] == 1, "an empty hash is not a password"
    assert s["distinct_people_with_auth_events"] == 1, "two logins by one person is one"
    assert s["auth_events_by_type"] == {"login": 2}


def test_a_missing_table_says_so(cx):
    cx.execute("DROP TABLE portal_opens")
    cx.commit()
    assert pu.summary(cx)["opens_by_kind"] == "table absent"


def test_it_only_reads():
    from pathlib import Path
    src = Path(pu.__file__).read_text().upper()
    for w in ("INSERT ", "UPDATE ", "DELETE ", "DROP "):
        assert w not in src, f"{w.strip()} in a read-only counter"
