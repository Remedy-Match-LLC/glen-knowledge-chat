"""The weekly invitation reads the real members board (review round 2, 2026-10-19).

Seeded through the modules' own writers into a fresh database, then read both ways:
through the sender's _board_emails and through the /api/console/members route."""
import importlib
import sqlite3

import pytest

import app as appmod
from dashboard import family_plan, household
from dashboard import subscriptions as subs

SECRET = "board-audience-secret"
TRIAL, FULL, PAUSED, CANC = ("trial@example.com", "full@example.com",
                             "paused@example.com", "cancelled@example.com")
HOLDER, COVERED = "holder@example.com", "covered@example.com"


def _weekly():
    return importlib.import_module("scripts.weekly_live_invitation")


def _membership(cx, email):
    return subs.create_membership(
        cx, email=email, stripe_customer_id="cus", stripe_payment_method_id="pm",
        amount_cents=9900, next_charge_date="2026-07-01")


@pytest.fixture
def board_db(tmp_path, monkeypatch):
    path = str(tmp_path / "chat_log.db")
    with sqlite3.connect(path) as cx:
        cx.row_factory = sqlite3.Row
        subs.init_subscriptions_table(cx)
        for fn in ("migrate_add_failed_count", "migrate_add_membership_columns",
                   "migrate_add_term_cap_column", "migrate_add_attribution_column",
                   "migrate_add_consent_column"):
            getattr(subs, fn)(cx)
        _membership(cx, TRIAL)
        subs.advance_after_charge(cx, _membership(cx, FULL))
        subs.set_skip_next(cx, _membership(cx, PAUSED), True)
        subs.set_status(cx, _membership(cx, CANC), "cancelled")
        family_plan.init_family_plan_table(cx)
        household.init_household_tables(cx)
        family_plan.activate(cx, HOLDER, next_charge_at=None, source="comp")
        household.add_member(cx, HOLDER, COVERED)
        cx.commit()
    monkeypatch.setattr(appmod, "LOG_DB", path)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    return path


def _route_emails():
    r = appmod.app.test_client().get(f"/api/console/members?key={SECRET}")
    assert r.status_code == 200, r.data
    return r, {row["email"].lower() for rows in r.get_json()["buckets"].values()
               for row in rows}


def test_the_sender_reads_everyone_the_board_route_shows(board_db):
    weekly = _weekly()
    emails = weekly._board_emails()
    assert emails == {TRIAL, FULL, PAUSED, HOLDER, COVERED}
    assert emails == _route_emails()[1]


def test_a_failed_family_read_stops_the_sender_and_only_degrades_the_route(board_db,
                                                                          monkeypatch):
    weekly = _weekly()

    def boom(cx):
        raise RuntimeError("family table unreadable")
    monkeypatch.setattr(family_plan, "list_active", boom)

    with pytest.raises(RuntimeError, match="family table unreadable"):
        weekly._board_emails()
    r, emails = _route_emails()
    assert r.get_json()["buckets"]["family"] == []
    assert emails == {TRIAL, FULL, PAUSED}
