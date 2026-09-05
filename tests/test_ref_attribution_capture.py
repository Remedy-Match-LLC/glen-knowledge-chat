"""An affiliate link or a referral code must produce an attribution the payout can see.

The gap this closes, found 2026-09-05: affiliate payout asks one question at settlement,
"is there a referral_events row for this buyer naming an approved affiliate?" Only three
things ever wrote one: the ScoreApp webhook (deprecated, last delivery 2026-07-18),
masterclass signups, and concierge signups.

An ordinary ?ref= visit wrote nothing. A redeemed referral code wrote nothing. So most
people who were genuinely referred earned their referrer no credit at all.

These tests run the real helper against a real SQLite schema, rather than reading source
strings, because the behaviour that matters here is what lands in the table.
"""
import re
import sqlite3
import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
SRC = APP.read_text()

DDL = """
CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, created_at TEXT, name TEXT,
  email TEXT UNIQUE, organization TEXT, website TEXT, promo_method TEXT,
  slug TEXT UNIQUE, token TEXT, status TEXT DEFAULT 'approved', notes TEXT,
  referred_by TEXT);
CREATE TABLE referral_events (id INTEGER PRIMARY KEY, received_at TEXT NOT NULL,
  lead_id INTEGER, email TEXT, first_name TEXT, last_name TEXT, utm_source TEXT DEFAULT '',
  utm_medium TEXT DEFAULT '', utm_campaign TEXT DEFAULT '', utm_content TEXT DEFAULT '',
  utm_term TEXT DEFAULT '', quiz_score TEXT DEFAULT '', raw_json TEXT DEFAULT '');
"""


def _load(cx):
    """Extract the helper and run it against `cx`, with the module's real deps stubbed."""
    start = SRC.index("def _capture_ref_attribution(")
    end = SRC.index("def _capture_concierge_referral(")
    import contextlib, threading

    class _DB:
        @staticmethod
        @contextlib.contextmanager
        def connect(_p):
            yield cx

    ns = {
        "re": re, "datetime": datetime.datetime, "timezone": datetime.timezone,
        "timedelta": datetime.timedelta, "db": _DB, "LOG_DB": ":memory:",
        "_db_lock": threading.Lock(), "print": lambda *a, **k: None,
        "_REF_SLUG_RE": re.compile(r"^[A-Za-z0-9_-]{1,64}$"),
        "_REF_COOKIE_MAX_AGE": 90 * 24 * 3600,
    }
    exec(compile(SRC[start:end], "app_excerpt", "exec"), ns)
    return ns["_capture_ref_attribution"]


def _fresh(approved=("alice", "bob")):
    cx = sqlite3.connect(":memory:")
    cx.executescript(DDL)
    for i, s in enumerate(approved):
        cx.execute("INSERT INTO affiliate_signups (id,email,slug,token,status) "
                   "VALUES (?,?,?,?, 'approved')", (i + 1, f"{s}@x.com", s, f"t{i}"))
    cx.commit()
    return cx


def _rows(cx):
    return cx.execute("SELECT email, utm_source, utm_medium FROM referral_events").fetchall()


def test_a_referral_link_now_creates_an_attribution():
    cx = _fresh()
    assert _load(cx)("buyer@x.com", "alice", medium="ref-link") is True
    assert _rows(cx) == [("buyer@x.com", "alice", "ref-link")]


def test_a_referral_code_now_creates_an_attribution():
    cx = _fresh()
    assert _load(cx)("buyer@x.com", "bob", medium="ref-code", campaign="BOB10") is True
    assert _rows(cx)[0][1:] == ("bob", "ref-code")


def test_first_touch_wins_within_the_window():
    """Glen's ruling. The payout reads the MOST RECENT row, so without this the write
    would silently invert it and hand the referral to whoever linked last."""
    cx = _fresh()
    f = _load(cx)
    assert f("buyer@x.com", "alice", medium="ref-link") is True
    assert f("buyer@x.com", "bob", medium="ref-code") is False
    assert [r[1] for r in _rows(cx)] == ["alice"], "bob took a referral already attributed"


def test_an_attribution_older_than_the_window_no_longer_blocks():
    cx = _fresh()
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=120)).isoformat()
    cx.execute("INSERT INTO referral_events (received_at,email,utm_source) VALUES (?,?,?)",
               (old, "buyer@x.com", "alice"))
    cx.commit()
    assert _load(cx)("buyer@x.com", "bob", medium="ref-link") is True
    assert [r[1] for r in _rows(cx)] == ["alice", "bob"]


def test_an_unapproved_slug_records_nothing():
    cx = _fresh()
    assert _load(cx)("buyer@x.com", "stranger", medium="ref-link") is False
    assert _rows(cx) == []


def test_junk_input_records_nothing_and_never_raises():
    cx = _fresh()
    f = _load(cx)
    for email, slug in (("", "alice"), ("b@x.com", ""), ("b@x.com", "has space"),
                        (None, None), ("b@x.com", "a" * 80)):
        assert f(email, slug, medium="ref-link") is False
    assert _rows(cx) == []
