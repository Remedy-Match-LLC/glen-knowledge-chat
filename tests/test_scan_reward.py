"""Scan share reward: the rules module and the ledger, on a real SQLite schema.

Plan: platform/plans/2026-11-06-scan-share-reward.md. Each test runs the real code
against real tables, because what matters is what lands in them. Tests tagged with
a review item number (R1..R14) pin the fixes from the two blind review rounds.
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


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _cx(approved=("alice", "bob"), history=True):
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
    CREATE TABLE email_aliases (alias_email TEXT PRIMARY KEY, canonical_email TEXT NOT NULL,
      merge_id INTEGER, created_at TEXT);
    """)
    if history:
        cx.executescript("""
        CREATE TABLE scan_freshness (email TEXT PRIMARY KEY, last_scan_date TEXT,
          updated_at TEXT);
        CREATE TABLE client_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
          scan_date TEXT, scan_id TEXT, synced_at TEXT);
        """)
    for i, s in enumerate(approved):
        cx.execute("INSERT INTO affiliate_signups (email, slug, token, status) "
                   "VALUES (?,?,?, 'approved')", (f"{s}@x.com", s, f"t{i}"))
    cx.commit()
    sr.init_tables(cx)
    return cx


def _invite(cx, email, slug, days_ago):
    cx.execute("INSERT INTO scan_invites (friend_email, slug, inviter_email, engaged_at) "
               "VALUES (?,?,?,?)", (email, slug, f"{slug}@x.com", _ago(days_ago)))
    cx.commit()


def _paid(cx, email, days_ago):
    cx.execute("INSERT INTO orders (email, status, pay_status, paid_at, created_at) "
               "VALUES (?, 'shipped', 'paid', ?, ?)", (email, _ago(days_ago), _ago(days_ago)))
    cx.commit()


def _alias(cx, alias, canon):
    cx.execute("INSERT INTO email_aliases (alias_email, canonical_email) VALUES (?,?)",
               (alias, canon))
    cx.commit()


def _notice(cx, msg_id, email, days_ago=0):
    return sr.record_scan_arrival(cx, msg_id=msg_id, email=email, client_name="F",
                                  noticed_at=_ago(days_ago))


# ── R5: decision 2, the hold ────────────────────────────────────────────────

def test_first_inviter_holds_for_90_days_after_first_engagement():
    eng = [("alice", _iso(0)), ("bob", _iso(10))]
    assert rules.holder_at(eng, [], _iso(90))["slug"] == "alice"
    assert rules.holder_at(eng, [], _iso(91)) is None


def test_takeover_needs_both_windows_passed():
    # alice engaged day 0, friend bought day 60: hold runs to day 150.
    eng = [("alice", _iso(0)), ("bob", _iso(120))]
    assert rules.holder_at(eng, [_iso(60)], _iso(130))["slug"] == "alice"
    eng2 = [("alice", _iso(0)), ("bob", _iso(151))]
    assert rules.holder_at(eng2, [_iso(60)], _iso(152))["slug"] == "bob"


def test_a_purchase_after_the_lapse_still_blocks_takeover_for_90_days():
    # R5, inverted from the first build: day 95 purchase is after alice's lapse at 90.
    eng = [("alice", _iso(0)), ("bob", _iso(150))]
    assert rules.holder_at(eng, [_iso(95)], _iso(160))["slug"] == "alice"
    eng2 = [("alice", _iso(0)), ("bob", _iso(186))]
    assert rules.holder_at(eng2, [_iso(95)], _iso(190))["slug"] == "bob"


def test_the_order_being_credited_does_not_extend_its_own_hold():
    eng = [("alice", _iso(0))]
    assert rules.holder_at(eng, [_iso(100)], _iso(100)) is None


def test_lapsed_inviter_earns_only_when_the_constant_says(monkeypatch):
    eng = [("alice", _iso(0))]
    assert rules.LAPSED_INVITER_KEEPS_EARNING is False
    assert rules.holder_at(eng, [], _iso(200)) is None
    monkeypatch.setattr(rules, "LAPSED_INVITER_KEEPS_EARNING", True)
    assert rules.holder_at(eng, [], _iso(200))["slug"] == "alice"


def test_same_inviter_reengaging_does_not_restart_the_clock():
    eng = [("alice", _iso(0)), ("alice", _iso(80))]
    assert rules.holder_at(eng, [], _iso(100)) is None


# ── R1, R2: recording the inviter ───────────────────────────────────────────

def test_invite_is_recorded_in_its_own_table_not_referral_events():
    cx = _cx()
    assert sr.record_invite(cx, "Friend@Y.com", "alice") == "recorded"
    assert cx.execute("SELECT friend_email, slug FROM scan_invites").fetchall() == [
        ("friend@y.com", "alice")]
    assert cx.execute("SELECT COUNT(*) FROM referral_events").fetchone()[0] == 0


@pytest.mark.parametrize("setup,why", [
    (lambda cx: _paid(cx, "friend@y.com", 30), "known-customer"),
    (lambda cx: cx.execute("INSERT INTO scan_freshness VALUES ('friend@y.com','2026-01-01','')"),
     "known-scanner"),
    (lambda cx: cx.execute("INSERT INTO referral_events (received_at, email, utm_source) "
                           "VALUES ('2026-01-01', 'friend@y.com', 'bob')"), "known-referral"),
])
def test_an_unverified_invite_cannot_claim_a_known_person(setup, why):
    cx = _cx()
    setup(cx)
    cx.commit()
    assert sr.record_invite(cx, "friend@y.com", "alice") == why
    assert cx.execute("SELECT COUNT(*) FROM scan_invites").fetchone()[0] == 0


def test_invite_refused_when_scan_history_cannot_be_established():
    cx = _cx(history=False)
    assert sr.record_invite(cx, "friend@y.com", "alice") == "known-scanner"


def test_second_inviter_is_not_recorded_while_the_first_holds():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 10)
    assert sr.record_invite(cx, "friend@y.com", "bob") == "held"
    assert cx.execute("SELECT COUNT(*) FROM scan_invites").fetchone()[0] == 1


def test_a_lapsed_hold_lets_a_new_inviter_record():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 200)
    assert sr.record_invite(cx, "friend@y.com", "bob") == "recorded"


def test_unapproved_and_self_referrals_are_refused():
    cx = _cx()
    assert sr.record_invite(cx, "friend@y.com", "mallory") == "not-approved"
    assert sr.record_invite(cx, "alice@x.com", "alice") == "self"
    assert sr.record_invite(cx, "friend@y.com", "") == "no-inviter"


# ── R3: identity ────────────────────────────────────────────────────────────

def test_an_alias_of_the_inviter_cannot_self_refer():
    cx = _cx()
    _alias(cx, "alice.other@z.com", "alice@x.com")
    assert sr.record_invite(cx, "alice.other@z.com", "alice") == "self"


def test_a_known_customer_under_an_alias_is_still_known():
    cx = _cx()
    _alias(cx, "old@y.com", "friend@y.com")
    _paid(cx, "old@y.com", 30)
    assert sr.record_invite(cx, "friend@y.com", "alice") == "known-customer"


def test_prior_scan_under_an_alias_makes_this_a_repeat():
    cx = _cx()
    _alias(cx, "old@y.com", "friend@y.com")
    cx.execute("INSERT INTO client_scans (email, scan_date) VALUES ('old@y.com', '2026-01-01')")
    cx.commit()
    _invite(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com") == "repeat"
    assert sr.balance(cx, "alice@x.com") == 0


def test_the_ledger_belongs_to_the_canonical_address():
    cx = _cx()
    _alias(cx, "alice.other@z.com", "alice@x.com")
    _invite(cx, "friend@y.com", "alice", 5)
    _notice(cx, "m1", "friend@y.com", 1)
    assert sr.balance(cx, "alice.other@z.com") == 1
    assert cx.execute("SELECT email FROM reading_credits").fetchone()[0] == "alice@x.com"


# ── Piece 2: the hub ────────────────────────────────────────────────────────

def test_scan_offer_leads_the_hub():
    offers = [{"name": "Your Front Door — start here", "description": "a"},
              {"name": "Free Bioenergetic Wellness Scan",
               "description": sr._ADVANCED_NOTE + "A free voice scan."}]
    out = sr.lead_with_scan(offers)
    assert out[0]["name"] == "Free Bioenergetic Wellness Scan"
    assert out[0]["primary"] is True
    assert out[0]["description"] == "A free voice scan."
    assert offers[1]["description"].startswith(sr._ADVANCED_NOTE)


# ── R7: spend ───────────────────────────────────────────────────────────────

def _earn(cx, email, key):
    sr._apply(cx, {"action": "credit", "email": email, "delta": 1, "reason": "t",
                   "event_key": key, "friend": "", "slug": "", "detail": ""})
    cx.commit()


def test_spend_never_goes_below_zero_and_is_idempotent_per_client():
    cx = _cx()
    _earn(cx, "alice@x.com", "k1")
    first = sr.spend(cx, "alice@x.com", key="report-7", by="console")
    assert first["ok"] and first["spent"] and first["balance"] == 0
    again = sr.spend(cx, "alice@x.com", key="report-7", by="console")
    assert again["duplicate"] and again["balance"] == 0
    empty = sr.spend(cx, "alice@x.com", key="report-8", by="console")
    assert not empty["ok"] and empty["balance"] == 0
    assert sr.portal_summary(cx, "alice@x.com") == {"balance": 0, "earned": 1, "used": 1}
    assert sr.portal_summary(cx, "nobody@x.com") is None


def test_a_spend_key_reused_for_another_client_is_a_conflict():
    cx = _cx()
    _earn(cx, "alice@x.com", "k1")
    _earn(cx, "bob@x.com", "k2")
    assert sr.spend(cx, "alice@x.com", key="report-7", by="console")["spent"]
    clash = sr.spend(cx, "bob@x.com", key="report-7", by="console")
    assert clash["conflict"] and not clash["ok"]
    assert sr.balance(cx, "bob@x.com") == 1


def test_the_spend_insert_itself_refuses_an_empty_balance():
    # The balance check is inside the INSERT, not a separate read before it.
    cx = _cx()
    out = sr.spend(cx, "alice@x.com", key="r1", by="console")
    assert not out["ok"]
    assert cx.execute("SELECT COUNT(*) FROM reading_credits").fetchone()[0] == 0


# ── R4, R6: first scans and arrivals ────────────────────────────────────────

def test_first_scan_credits_the_inviter_once_and_only_the_first():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com", 2) == "first"
    assert _notice(cx, "m1", "friend@y.com", 2) == "duplicate"
    assert _notice(cx, "m2", "friend@y.com", 0) == "repeat"
    assert sr.balance(cx, "alice@x.com") == 1


def test_a_prior_scan_the_same_day_counts_as_prior():
    cx = _cx()
    today = datetime.now(timezone.utc).date().isoformat()
    cx.execute("INSERT INTO scan_freshness VALUES ('friend@y.com', ?, '')", (today,))
    cx.commit()
    _invite(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com") == "repeat"
    assert sr.balance(cx, "alice@x.com") == 0


def test_no_scan_history_store_means_unknown_and_no_credit():
    cx = _cx(history=False)
    _invite(cx, "friend@y.com", "alice", 5)
    assert _notice(cx, "m1", "friend@y.com") == "unknown"
    assert sr.balance(cx, "alice@x.com") == 0


class _FailingCx:
    """A connection whose scan-history query fails."""
    def __init__(self, cx):
        self._cx = cx

    def execute(self, sql, params=()):
        if "FROM client_scans" in sql:
            raise sqlite3.OperationalError("boom")
        return self._cx.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cx, name)


def test_a_history_query_failure_means_unknown_and_no_credit():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 5)
    assert sr.scan_history(_FailingCx(cx), {"friend@y.com"}, _ago(0)) == "unknown"
    assert _notice(_FailingCx(cx), "m1", "friend@y.com") == "unknown"
    assert sr.balance(cx, "alice@x.com") == 0


def test_a_scan_before_the_invite_earns_nothing():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 1)
    assert _notice(cx, "m1", "friend@y.com", 3) == "first"
    assert sr.balance(cx, "alice@x.com") == 0


def test_arrival_and_credit_commit_together_or_not_at_all(monkeypatch):
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 5)
    real = sr._apply

    def boom(*a, **k):
        raise RuntimeError("credit write failed")
    monkeypatch.setattr(sr, "_apply", boom)
    with pytest.raises(RuntimeError):
        _notice(cx, "m1", "friend@y.com", 1)
    assert cx.execute("SELECT COUNT(*) FROM scan_arrivals").fetchone()[0] == 0
    monkeypatch.setattr(sr, "_apply", real)
    assert _notice(cx, "m1", "friend@y.com", 1) == "first"     # the retry completes
    assert sr.balance(cx, "alice@x.com") == 1


def test_scan_arrival_marks_the_e4l_account_on_an_existing_person_only():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('friend@y.com')")
    cx.commit()
    _notice(cx, "m1", "friend@y.com")
    _notice(cx, "m2", "stranger@y.com")
    assert cx.execute("SELECT e4l_account_at FROM people WHERE email='friend@y.com'"
                      ).fetchone()[0]
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1


# ── R8, R9, ORDER_CREDIT_TO: order credits ──────────────────────────────────

CATALOG = {"es1-lymph": {"name": "ES1 Lymph Infoceutical"},
           "myelin": {"name": "Myelin Syntropy Powder"}}


def _order(email="friend@y.com", qty=1, paid_days_ago=0, created_days_ago=None):
    o = {"email": email, "items": [{"slug": "es1-lymph", "qty": qty}]}
    if paid_days_ago is not None:
        o["paid_at"] = _ago(paid_days_ago)
    if created_days_ago is not None:
        o["created_at"] = _ago(created_days_ago)
    return o


@pytest.fixture
def to_inviter(monkeypatch):
    monkeypatch.setattr(rules, "ORDER_CREDIT_TO", "inviter")


def test_order_credit_defaults_to_the_buyer_and_needs_no_invite():
    assert rules.ORDER_CREDIT_TO == "buyer"
    cx = _cx()
    assert sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)["credited"]
    assert not sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)["credited"]
    assert sr.balance(cx, "friend@y.com") == 1


def test_inviter_mode_credits_the_holder_once_per_order(to_inviter):
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 5)
    assert sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)["credited"]
    assert not sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)["credited"]
    assert sr.credit_store_order(cx, _order(), "INV-2", catalog=CATALOG)["credited"]
    assert sr.balance(cx, "alice@x.com") == 2
    assert sr.balance(cx, "friend@y.com") == 0


def test_inviter_mode_steps_aside_when_system_a_owns_the_buyer(to_inviter):
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 5)
    cx.execute("INSERT INTO referral_events (received_at, email, utm_source) "
               "VALUES (?, 'friend@y.com', 'bob')", (_ago(3),))
    cx.commit()
    out = sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)
    assert out["why"] == "system-a" and sr.balance(cx, "alice@x.com") == 0


def test_an_unknown_recipient_setting_is_refused(monkeypatch):
    monkeypatch.setattr(rules, "ORDER_CREDIT_TO", "both")
    with pytest.raises(ValueError):
        sr.credit_store_order(_cx(), _order(), "INV-1", catalog=CATALOG)


def test_order_time_is_paid_at_else_created_at_never_now(to_inviter):
    cx = _cx()
    # alice engaged 200 days ago, so her hold lapsed 110 days ago. An order created
    # 150 days ago sat inside it. Judged at retry time (now) it would find no holder.
    _invite(cx, "friend@y.com", "alice", 200)
    o = _order(paid_days_ago=None, created_days_ago=150)
    assert sr.credit_store_order(cx, o, "INV-1", catalog=CATALOG)["credited"]
    none = _order(paid_days_ago=None)
    assert sr.credit_store_order(cx, none, "INV-2", catalog=CATALOG)["why"] == "no-purchase-time"


def test_a_no_holder_decision_is_final_on_retry(to_inviter):
    cx = _cx()
    o = _order(paid_days_ago=2)
    assert sr.credit_store_order(cx, o, "INV-1", catalog=CATALOG)["why"] == "no-holder"
    _invite(cx, "friend@y.com", "bob", 1)          # an inviter appears later
    retry = sr.credit_store_order(cx, o, "INV-1", catalog=CATALOG)
    assert retry["why"] == "decided:no-holder"
    assert sr.balance(cx, "bob@x.com") == 0


def test_qty_zero_and_wholesale_and_non_infoceutical_never_qualify():
    cx = _cx()
    assert sr.credit_store_order(cx, _order(qty=0), "INV-1", catalog=CATALOG)["why"] == \
        "not-qualifying"
    plain = {"email": "friend@y.com", "paid_at": _ago(0),
             "items": [{"slug": "myelin", "qty": 1}]}
    assert sr.credit_store_order(cx, plain, "INV-2", catalog=CATALOG)["why"] == "not-qualifying"
    assert sr.credit_store_order(cx, _order(), "INV-3", catalog=CATALOG,
                                 is_wholesale=True)["why"] == "not-qualifying"
    assert sr.balance(cx, "friend@y.com") == 0


def test_inviter_mode_with_no_inviter_means_no_credit(to_inviter):
    cx = _cx()
    assert sr.credit_store_order(cx, _order(), "INV-1", catalog=CATALOG)["why"] == "no-holder"


# ── Pieces 3 and 4, R10: parsing and ingesting the notice ───────────────────

def test_parse_scan_notice():
    p = sr.parse_scan_notice("Energy4Life - New voice scan from Ann Bauder",
                             "A new scan is waiting for you from Ann Bauder (Ann@Example.com).")
    assert p == {"email": "ann@example.com", "client_name": "Ann Bauder"}
    assert sr.parse_scan_notice("E4L Portal - New Client Sign Up", "x (a@b.com)") is None


class _Svc:
    """A paged Gmail stub that records every call, so a test can prove it is read
    only. Pages arrive newest first, as Gmail returns them."""
    def __init__(self, pages):
        self.pages = pages
        self.by_id = {m["id"]: m for page in pages for m in page}
        self.calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        self.calls.append("list")
        i = int(kw.get("pageToken") or 0)
        self._r = {"messages": [{"id": m["id"]} for m in self.pages[i]]}
        if i + 1 < len(self.pages):
            self._r["nextPageToken"] = str(i + 1)
        return self

    def get(self, **kw):
        self.calls.append("get")
        self._r = self.by_id[kw["id"]]
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


def test_ingest_pages_through_everything_oldest_first_read_only():
    cx = _cx()
    _invite(cx, "friend@y.com", "alice", 10)
    now = datetime.now(timezone.utc)
    pages = [[_msg(f"n{i}", f"other{i}@y.com", now - timedelta(hours=i)) for i in range(100)],
             [_msg("mid", "friend@y.com", now - timedelta(days=1))],
             [_msg("oldest", "friend@y.com", now - timedelta(days=3))]]
    svc = _Svc(pages)
    c1 = sr.ingest_scan_notices(cx, service=svc)
    assert c1["listed"] == 102 and svc.calls.count("list") == 3
    assert cx.execute("SELECT history FROM scan_arrivals WHERE gmail_msg_id='oldest'"
                      ).fetchone()[0] == "first"
    assert cx.execute("SELECT history FROM scan_arrivals WHERE gmail_msg_id='mid'"
                      ).fetchone()[0] == "repeat"
    c2 = sr.ingest_scan_notices(cx, service=svc)
    assert c2["duplicate"] == 102
    assert set(svc.calls) <= {"list", "get"}
    assert sr.balance(cx, "alice@x.com") == 1


# ── R11: the one-time fill ──────────────────────────────────────────────────

def test_fill_dry_run_alters_nothing():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('a@y.com')")
    cx.commit()
    dry = sr.fill_e4l_client_ids(cx, [{"email": "a@y.com", "client_id": 1}])
    assert dry["would_set"] == 1 and dry["set"] == 0
    cols = [r[1] for r in cx.execute("PRAGMA table_info(people)")]
    assert "e4l_client_id" not in cols
    assert not cx.in_transaction


def test_fill_applies_only_on_true_and_never_overwrites():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('a@y.com'), ('b@y.com')")
    cx.commit()
    sr.ensure_people_columns(cx)
    cx.execute("UPDATE people SET e4l_client_id='99' WHERE email='b@y.com'")
    cx.commit()
    rows = [{"email": "a@y.com", "client_id": 1}, {"email": "b@y.com", "client_id": 2},
            {"email": "c@y.com", "client_id": 3}]
    assert sr.fill_e4l_client_ids(cx, rows, apply="true")["set"] == 0
    applied = sr.fill_e4l_client_ids(cx, rows, apply=True)
    assert (applied["set"], applied["already_set"], applied["unmatched"]) == (1, 1, 1)
    got = dict(cx.execute("SELECT email, e4l_client_id FROM people").fetchall())
    assert got == {"a@y.com": "1", "b@y.com": "99"}


def test_fill_refuses_ambiguous_people_and_null_ids():
    cx = _cx()
    cx.execute("INSERT INTO people (email) VALUES ('a@y.com'), ('d@y.com')")
    cx.commit()
    _alias(cx, "a.old@y.com", "a@y.com")
    rows = [{"email": "a@y.com", "client_id": 1}, {"email": "a.old@y.com", "client_id": 2},
            {"email": "d@y.com", "client_id": None}, {"email": "d@y.com", "client_id": "None"}]
    out = sr.fill_e4l_client_ids(cx, rows, apply=True)
    assert (out["ambiguous"], out["bad"], out["set"]) == (1, 2, 0)


def test_flag_reads_the_environment(monkeypatch):
    monkeypatch.delenv(rules.FLAG_ENV, raising=False)
    assert not sr.enabled()
    monkeypatch.setenv(rules.FLAG_ENV, "1")
    assert sr.enabled()
