"""The members board must show everyone who actually holds membership.

It read `subscriptions` only (kind='membership', status='active'), but entitlement
lives in the `memberships` table -- that is what _active_membership_for_email, the
portal's Member checkmark and the pricing gate all read. Anyone holding a GRANT
without a SUBSCRIPTION row was invisible:

  * one-time month and annual-prepay purchases (billing 'one_time' -> no sub row)
  * manual console enrols (the endpoint says it creates no subscriptions row)
  * biofield care-taster grants -- "did a biofield in the last month"
  * the biofield month-on-delivery grant

Observed live 2026-09-02: the board reported {full: 0} while grants existed, and
stayed at 0 after 14 successful enrols.
"""
import sqlite3

import pytest

from dashboard import subscriptions as subs


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "m.db"))
    cx.row_factory = sqlite3.Row
    cx.execute("""CREATE TABLE subscriptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, kind TEXT, status TEXT,
        amount_cents INTEGER, created_at TEXT, next_charge_date TEXT,
        cadence_months INTEGER, order_count INTEGER, skip_next INTEGER)""")
    cx.execute("""CREATE TABLE memberships(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, source TEXT,
        granted_at TEXT, expires_at TEXT)""")
    return cx


def test_a_grant_without_a_subscription_still_appears(tmp_path):
    cx = _db(tmp_path)
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('paid@x.com','membership_year_prepay','2026-01-01T00:00:00Z','2027-01-01T00:00:00Z')")
    cx.commit()
    rows = subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z")
    assert [r["email"] for r in rows] == ["paid@x.com"]
    assert rows[0]["category"] == "full"
    assert rows[0]["source"] == "membership_year_prepay"
    assert rows[0]["expires_at"].startswith("2027-01-01")


def test_an_expired_grant_does_not_appear(tmp_path):
    cx = _db(tmp_path)
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('old@x.com','membership_month','2025-01-01T00:00:00Z','2025-02-01T00:00:00Z')")
    cx.commit()
    assert subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z") == []


def test_a_lifetime_grant_with_no_expiry_appears(tmp_path):
    cx = _db(tmp_path)
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('life@x.com','membership_founding','2024-01-01T00:00:00Z',NULL)")
    cx.commit()
    rows = subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z")
    assert [r["email"] for r in rows] == ["life@x.com"]


def test_the_furthest_expiry_wins_for_one_email(tmp_path):
    """Grants are additive; a member with two rows is ONE person, shown at the
    later date -- the same rule _active_membership_for_email applies."""
    cx = _db(tmp_path)
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('dup@x.com','membership_care_taster','2026-08-01T00:00:00Z','2026-09-30T00:00:00Z'),"
               "('dup@x.com','membership_year_monthly','2026-09-02T00:00:00Z','2027-09-06T00:00:00Z')")
    cx.commit()
    rows = subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["expires_at"].startswith("2027-09-06")


def test_a_biofield_taster_is_labelled_as_one(tmp_path):
    """Glen asked for the biofield-in-the-last-month people specifically; the
    source has to survive to the board or he cannot tell them apart."""
    cx = _db(tmp_path)
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('bf@x.com','care_taster','2026-08-20T00:00:00Z','2026-09-19T00:00:00Z')")
    cx.commit()
    rows = subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z")
    assert rows[0]["source"] == "care_taster"


def test_a_subscription_holder_is_not_listed_twice(tmp_path):
    """Someone with BOTH a sub and a grant is one person. The sub row wins, since
    it carries the billing state (paused, next charge) a grant has no idea about."""
    cx = _db(tmp_path)
    cx.execute("INSERT INTO subscriptions(email,kind,status,amount_cents,created_at,"
               "next_charge_date,cadence_months,order_count,skip_next) VALUES"
               "('both@x.com','membership','active',9900,'2026-05-01T00:00:00Z',"
               "'2026-10-01',1,3,0)")
    cx.execute("INSERT INTO memberships(email,source,granted_at,expires_at) VALUES"
               "('both@x.com','membership_month','2026-08-01T00:00:00Z','2027-08-01T00:00:00Z')")
    cx.commit()
    subbed = {(s.get("email") or "").lower() for s in subs.list_active_memberships(cx)}
    extra = [r for r in subs.list_membership_holders(cx, now="2026-09-02T00:00:00Z")
             if r["email"].lower() not in subbed]
    assert extra == [], "a subscription holder must not also appear as a grant row"
