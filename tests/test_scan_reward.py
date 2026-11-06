"""Scan share reward: the rules module and the ledger, on a real SQLite schema.

Plan: platform/plans/2026-11-06-scan-share-reward.md. Each test runs the real code
against real tables, because what matters is what lands in them.
"""
import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import scan_reward as sr
from dashboard import scan_reward_rules as rules

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _iso(days):
    return (T0 + timedelta(days=days)).isoformat()


def _cx(approved=("alice", "bob")):
    cx = sqlite3.connect(":memory:")
    cx.executescript("""
    CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, created_at TEXT, name TEXT,
      email TEXT UNIQUE, slug TEXT UNIQUE, token TEXT, status TEXT DEFAULT 'approved');
    CREATE TABLE referral_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
      received_at TEXT NOT NULL, lead_id INTEGER, email TEXT, first_name TEXT,
      last_name TEXT, utm_source TEXT DEFAULT '', utm_medium TEXT DEFAULT '',
      utm_campaign TEXT DEFAULT '', utm_content TEXT DEFAULT '', utm_term TEXT DEFAULT '',
      quiz_score TEXT DEFAULT '', raw_json TEXT DEFAULT '');
    CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
      status TEXT, pay_status TEXT, paid_at TEXT, created_at TEXT);
    CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE,
      name TEXT DEFAULT '', roles TEXT DEFAULT '[]', tags TEXT DEFAULT '[]');
    """)
    for i, s in enumerate(approved):
        cx.execute("INSERT INTO affiliate_signups (email, slug, token, status) "
                   "VALUES (?,?,?, 'approved')", (f"{s}@x.com", s, f"t{i}"))
    cx.commit()
    sr.init_tables(cx)
    return cx


def _paid(cx, email, days_ago):
    t = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cx.execute("INSERT INTO orders (email, status, pay_status, paid_at, created_at) "
               "VALUES (?, 'shipped', 'paid', ?, ?)", (email, t, t))
    cx.commit()


# ── Decision 2: the hold ────────────────────────────────────────────────────

def test_first_inviter_holds_for_90_days_after_first_engagement():
    eng = [("alice", _iso(0)), ("bob", _iso(10))]
    assert rules.holder_at(eng, [], _iso(89))["slug"] == "alice"
    assert rules.holder_at(eng, [], _iso(90))["slug"] == "alice"
    assert rules.holder_at(eng, [], _iso(91)) is None


def test_a_purchase_extends_the_hold_90_days_after_it():
    eng = [("alice", _iso(0)), ("bob", _iso(100))]
    assert rules.holder_at(eng, [_iso(60)], _iso(149))["slug"] == "alice"
    # bob engaged at day 100, inside alice's extended hold, so he gets nothing
    assert rules.holder_at(eng, [_iso(60)], _iso(150))["slug"] == "alice"
    assert rules.holder_at(eng, [_iso(60)], _iso(151)) is None


def test_after_the_hold_lapses_the_next_inviter_takes_over():
    eng = [("alice", _iso(0)), ("bob", _iso(120))]
    h = rules.holder_at(eng, [], _iso(130))
    assert h["slug"] == "bob"


def test_same_inviter_reengaging_does_not_restart_the_clock():
    eng = [("alice", _iso(0)), ("alice", _iso(80))]
    assert rules.holder_at(eng, [], _iso(100)) is None


def test_a_purchase_after_the_lapse_does_not_revive_the_hold():
    eng = [("alice", _iso(0))]
    assert rules.holder_at(eng, [_iso(95)], _iso(100)) is None


# ── Piece 1: recording the inviter ──────────────────────────────────────────

def test_invite_is_recorded_without_terms():
    cx = _cx()
    assert sr.record_invite(cx, "Friend@Y.com", "alice") == "recorded"
    rows = cx.execute("SELECT email, utm_source, utm_medium FROM referral_events").fetchall()
    assert rows == [("friend@y.com", "alice", rules.REFERRAL_MEDIUM)]


def test_second_inviter_is_not_recorded_while_the_first_holds():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 10)
    assert sr.record_invite(cx, "friend@y.com", "bob") == "held"
    assert cx.execute("SELECT COUNT(*) FROM referral_events").fetchone()[0] == 1


def test_a_lapsed_hold_lets_a_new_inviter_record():
    cx = _cx()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    cx.execute("INSERT INTO referral_events (received_at, email, utm_source) "
               "VALUES (?, 'friend@y.com', 'alice')", (old,))
    cx.commit()
    assert sr.record_invite(cx, "friend@y.com", "bob") == "recorded"


def test_a_recent_purchase_keeps_an_old_inviter_holding():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 120)
    _paid(cx, "friend@y.com", 60)        # inside alice's first 90 days
    assert sr.record_invite(cx, "friend@y.com", "bob") == "held"
    assert sr.holder_for(cx, "friend@y.com")["slug"] == "alice"


def test_unapproved_and_self_referrals_are_refused():
    cx = _cx()
    assert sr.record_invite(cx, "friend@y.com", "mallory") == "not-approved"
    assert sr.record_invite(cx, "alice@x.com", "alice") == "self"
    assert sr.record_invite(cx, "friend@y.com", "") == "no-inviter"
    assert cx.execute("SELECT COUNT(*) FROM referral_events").fetchone()[0] == 0


# ── Piece 2: the hub ────────────────────────────────────────────────────────

def test_scan_offer_leads_the_hub():
    offers = [{"name": "Your Front Door — start here", "description": "a"},
              {"name": "Free Bioenergetic Wellness Scan",
               "description": sr._ADVANCED_NOTE + "A free voice scan."}]
    out = sr.lead_with_scan(offers)
    assert out[0]["name"] == "Free Bioenergetic Wellness Scan"
    assert out[0]["primary"] is True
    assert out[0]["description"] == "A free voice scan."
    assert offers[1]["description"].startswith(sr._ADVANCED_NOTE)   # input untouched


# ── Piece 5: the ledger ─────────────────────────────────────────────────────

def test_ledger_is_idempotent_per_event_and_spend_needs_a_balance():
    cx = _cx()
    assert sr._write(cx, email="alice@x.com", delta=1, reason="r", event_key="k1")
    assert not sr._write(cx, email="alice@x.com", delta=1, reason="r", event_key="k1")
    assert sr.balance(cx, "alice@x.com") == 1
    first = sr.spend(cx, "alice@x.com", event_key="report-7", by="console")
    assert first["ok"] and first["spent"] and first["balance"] == 0
    again = sr.spend(cx, "alice@x.com", event_key="report-7", by="console")
    assert again["duplicate"] and again["balance"] == 0
    empty = sr.spend(cx, "alice@x.com", event_key="report-8", by="console")
    assert not empty["ok"] and empty["balance"] == 0
    assert sr.portal_summary(cx, "alice@x.com") == {"balance": 0, "earned": 1, "used": 1}
    assert sr.portal_summary(cx, "nobody@x.com") is None


# ── Piece 6: crediting ──────────────────────────────────────────────────────

def _now_ref(cx, email, slug, days_ago=1):
    cx.execute("INSERT INTO referral_events (received_at, email, utm_source) VALUES (?,?,?)",
               ((datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
                email, slug))
    cx.commit()


def _notice(cx, msg_id, email, days_ago=0):
    return sr.record_scan_arrival(
        cx, msg_id=msg_id, email=email, client_name="F",
        noticed_at=(datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat())


def test_first_scan_credits_the_inviter_once_and_only_the_first():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com", 2) == "first"
    assert _notice(cx, "m1", "friend@y.com", 2) == "duplicate"
    assert _notice(cx, "m2", "friend@y.com", 0) == "repeat"
    assert sr.balance(cx, "alice@x.com") == 1


def test_a_friend_with_an_earlier_scan_on_record_earns_nothing():
    cx = _cx()
    cx.execute("CREATE TABLE scan_freshness (email TEXT PRIMARY KEY, last_scan_date TEXT, "
               "updated_at TEXT)")
    cx.execute("INSERT INTO scan_freshness VALUES ('friend@y.com', '2026-08-01', '')")
    cx.commit()
    _now_ref(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com") == "repeat"
    assert sr.balance(cx, "alice@x.com") == 0


def test_a_scan_before_the_invite_earns_nothing():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 1)
    assert _notice(cx, "m1", "friend@y.com", 3) == "first"
    assert sr.balance(cx, "alice@x.com") == 0


def test_scan_arrival_marks_the_e4l_account_on_an_existing_person_only():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('friend@y.com')")
    cx.commit()
    _notice(cx, "m1", "friend@y.com")
    _notice(cx, "m2", "stranger@y.com")
    assert cx.execute("SELECT e4l_account_at FROM people WHERE email='friend@y.com'"
                      ).fetchone()[0]
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1


CATALOG = {"es1-lymph": {"name": "ES1 Lymph Infoceutical"},
           "myelin": {"name": "Myelin Syntropy Powder"}}


def test_a_qualifying_store_order_credits_once_per_order():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 5)
    order = {"email": "friend@y.com", "items": [{"slug": "es1-lymph", "qty": 1}],
             "paid_at": datetime.now(timezone.utc).isoformat()}
    assert sr.credit_store_order(cx, order, "INV-1", catalog=CATALOG)["credited"]
    assert not sr.credit_store_order(cx, order, "INV-1", catalog=CATALOG)["credited"]
    assert sr.credit_store_order(cx, order, "INV-2", catalog=CATALOG)["credited"]
    assert sr.balance(cx, "alice@x.com") == 2


def test_non_qualifying_and_wholesale_orders_earn_nothing():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 5)
    plain = {"email": "friend@y.com", "items": [{"slug": "myelin", "qty": 1}]}
    assert sr.credit_store_order(cx, plain, "INV-1", catalog=CATALOG)["why"] == "not-qualifying"
    info = {"email": "friend@y.com", "items": [{"slug": "es1-lymph", "qty": 1}]}
    assert not sr.credit_store_order(cx, info, "INV-2", catalog=CATALOG,
                                     is_wholesale=True)["credited"]
    assert sr.balance(cx, "alice@x.com") == 0


def test_no_inviter_means_no_credit():
    cx = _cx()
    info = {"email": "friend@y.com", "items": [{"slug": "es1-lymph", "qty": 1}]}
    assert sr.credit_store_order(cx, info, "INV-1", catalog=CATALOG)["why"] == "no-holder"


# ── Pieces 3 and 4: parsing and ingesting the notice ────────────────────────

def test_parse_scan_notice():
    p = sr.parse_scan_notice("Energy4Life - New voice scan from Ann Bauder",
                             "A new scan is waiting for you from Ann Bauder (Ann@Example.com).")
    assert p == {"email": "ann@example.com", "client_name": "Ann Bauder"}
    assert sr.parse_scan_notice("E4L Portal - New Client Sign Up", "x (a@b.com)") is None


class _Svc:
    """A Gmail service stub that records every call, so the test can prove it is
    read only."""
    def __init__(self, messages):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        self.calls.append("list")
        self._r = {"messages": [{"id": i} for i in self.messages_by_id]}
        return self

    def get(self, **kw):
        self.calls.append("get")
        self._r = self.messages_by_id[kw["id"]]
        return self

    def execute(self):
        return self._r


def _msg(mid, email, when):
    body = base64.urlsafe_b64encode(
        f"Your scan is waiting for you from F ({email})".encode()).decode()
    return {"id": mid, "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "body": {"data": body},
                        "headers": [{"name": "From", "value": "Energy4Life <noreply@e4l.com>"},
                                    {"name": "Subject",
                                     "value": "Energy4Life - New voice scan from F"}]}}


def test_ingest_is_read_only_oldest_first_and_rerunnable():
    cx = _cx()
    _now_ref(cx, "friend@y.com", "alice", 10)
    now = datetime.now(timezone.utc)
    svc = _Svc([_msg("new", "friend@y.com", now), _msg("old", "friend@y.com",
                                                      now - timedelta(days=2))])
    c1 = sr.ingest_scan_notices(cx, service=svc)
    assert (c1["first"], c1["repeat"]) == (1, 1)
    assert cx.execute("SELECT gmail_msg_id FROM scan_arrivals WHERE first_scan=1"
                      ).fetchone()[0] == "old"
    c2 = sr.ingest_scan_notices(cx, service=svc)
    assert c2["duplicate"] == 2
    assert set(svc.calls) <= {"list", "get"}
    assert sr.balance(cx, "alice@x.com") == 1


# ── The one-time fill ───────────────────────────────────────────────────────

def test_fill_counts_by_default_and_never_overwrites():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('a@y.com'), ('b@y.com')")
    cx.commit()
    sr.ensure_people_columns(cx)
    cx.execute("UPDATE people SET e4l_client_id='99' WHERE email='b@y.com'")
    cx.commit()
    rows = [{"email": "a@y.com", "client_id": 1}, {"email": "b@y.com", "client_id": 2},
            {"email": "c@y.com", "client_id": 3}]
    dry = sr.fill_e4l_client_ids(cx, rows)
    assert (dry["would_set"], dry["already_set"], dry["unmatched"], dry["set"]) == (1, 1, 1, 0)
    assert cx.execute("SELECT e4l_client_id FROM people WHERE email='a@y.com'"
                      ).fetchone()[0] is None
    applied = sr.fill_e4l_client_ids(cx, rows, apply=True)
    assert applied["set"] == 1
    got = dict(cx.execute("SELECT email, e4l_client_id FROM people").fetchall())
    assert got == {"a@y.com": "1", "b@y.com": "99"}


def test_flag_reads_the_environment(monkeypatch):
    monkeypatch.delenv(rules.FLAG_ENV, raising=False)
    assert not sr.enabled()
    monkeypatch.setenv(rules.FLAG_ENV, "1")
    assert sr.enabled()
