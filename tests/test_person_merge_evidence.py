"""Evidence of which address a person uses, and the suggested survivor.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import sqlite3
from datetime import datetime, timezone

import pytest

from dashboard import person_merge_evidence as ev

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)
AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


def _e(**kw):
    base = {k: None for k in ev.SIGNALS}
    base.update(kw)
    return base


def test_a_reply_outranks_more_recent_clicks():
    s = ev.suggest({AOL: _e(click="2026-09-25"), GMAIL: _e(reply="2026-08-01")}, AOL, NOW)
    assert s["survivor"] == GMAIL
    assert "reply" in s["survivor_reason"]


def test_the_later_reply_wins():
    s = ev.suggest({AOL: _e(reply="2026-09-20"), GMAIL: _e(reply="2026-07-01")}, GMAIL, NOW)
    assert s["survivor"] == AOL


def test_no_signals_keeps_the_hint():
    s = ev.suggest({AOL: _e(), GMAIL: _e()}, GMAIL, NOW)
    assert s["survivor"] == GMAIL


@pytest.mark.parametrize("days,want", [(89, "keep"), (91, "stop")])
def test_old_address_activity_decides_mailing(days, want):
    from datetime import timedelta
    when = (NOW - timedelta(days=days)).date().isoformat()
    s = ev.suggest({AOL: _e(click=when), GMAIL: _e(reply="2026-09-20")}, GMAIL, NOW)
    assert s["survivor"] == GMAIL
    assert s["mail_old"] == want
    assert s["mail_reason"]


def test_gather_reads_each_source(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.execute("CREATE TABLE portal_auth_events (event_id TEXT PRIMARY KEY, person_id INTEGER, "
               "email_hash TEXT, event TEXT, created_at TEXT)")
    cx.execute("CREATE TABLE auth_tokens (token_hash TEXT, email TEXT, purpose TEXT, extra TEXT, "
               "created_at TEXT, expires_at TEXT, consumed_at TEXT)")
    cx.execute("CREATE TABLE cadence_clicks (id INTEGER PRIMARY KEY, email TEXT, campaign_key TEXT, "
               "dest_key TEXT, clicked_at TEXT, user_agent TEXT)")
    cx.execute("CREATE TABLE client_scans (email TEXT, scan_date TEXT)")
    cx.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, created_at TEXT)")
    cx.execute("CREATE TABLE intake_responses (email TEXT PRIMARY KEY, created_at TEXT, "
               "submitted_at TEXT)")
    cx.execute("CREATE TABLE evox_bookings (id INTEGER PRIMARY KEY, email TEXT, start_ts TEXT)")
    cx.execute("INSERT INTO portal_auth_events VALUES ('e1', 7, 'h', 'login_succeeded', '2026-09-01')")
    cx.execute("INSERT INTO auth_tokens VALUES ('t1', ?, 'client_magic_link', '', '2026-09-10', "
               "'x', '2026-09-11')", (AOL,))
    cx.execute("INSERT INTO cadence_clicks VALUES (1, ?, 'c', 'd', '2026-09-12', '')", (AOL,))
    cx.execute("INSERT INTO client_scans VALUES (?, '2026-09-19')", (AOL,))
    cx.execute("INSERT INTO orders VALUES (1, ?, '2026-08-02')", (AOL,))
    cx.execute("INSERT INTO intake_responses VALUES (?, '2026-07-01', '2026-07-03')", (AOL,))
    cx.execute("INSERT INTO evox_bookings VALUES (1, ?, '2026-09-28T14:30:00')", (AOL,))
    got = ev.gather(cx, AOL, 7, reply_lookup=lambda e: "2026-09-20T00:00:00+00:00")
    assert got == {"reply": "2026-09-20T00:00:00+00:00", "signin": "2026-09-11",
                   "signin_link": "2026-09-10", "click": "2026-09-12", "scan": "2026-09-19",
                   "order": "2026-08-02", "intake": "2026-07-03",
                   "booking": "2026-09-28T14:30:00"}


def test_gather_with_missing_tables_gives_nothing(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    got = ev.gather(cx, AOL, 7, reply_lookup=lambda e: None)
    assert got == {k: None for k in ev.SIGNALS}


def test_mailbox_search_only_lists_and_reads_metadata():
    calls = []

    class Req:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class Msgs:
        def list(self, **kw):
            calls.append(("list", kw))
            return Req({"messages": [{"id": "m1"}]})

        def get(self, **kw):
            calls.append(("get", kw))
            return Req({"internalDate": "1790000000000"})

        def modify(self, **kw):
            raise AssertionError("the merge must never change mail")

    class Users:
        def messages(self):
            return Msgs()

    class Svc:
        def users(self):
            return Users()

    got = ev.gmail_last_reply(AOL, service=Svc())
    assert got.startswith("2026-")
    assert [c[0] for c in calls] == ["list", "get"]
    assert calls[1][1]["format"] == "metadata"
