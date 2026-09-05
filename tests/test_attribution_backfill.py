"""The attribution backfill must preview honestly and never over-credit.

Two properties matter more than the counts:
  1. A dry run writes NOTHING, and previews exactly what a real run would do. They share
     one code path so the preview cannot drift from the act.
  2. FIRST TOUCH holds across the whole run, not per row. Replaying history out of order
     would hand each referral to whoever appears last in the data rather than first in
     time, silently inverting Glen's ruling on historic data.
"""
import sqlite3
import pytest
from dashboard import attribution_backfill as ab

DDL = """
CREATE TABLE affiliate_signups (id INTEGER PRIMARY KEY, email TEXT, slug TEXT, status TEXT);
CREATE TABLE referral_events (id INTEGER PRIMARY KEY, received_at TEXT, lead_id INTEGER,
  email TEXT, first_name TEXT, last_name TEXT, utm_source TEXT, utm_medium TEXT,
  utm_campaign TEXT, utm_content TEXT, utm_term TEXT, quiz_score TEXT, raw_json TEXT);
CREATE TABLE inquiries (id TEXT PRIMARY KEY, created_at TEXT, client_email TEXT, ref_slug TEXT);
CREATE TABLE referral_redemptions (referee_email TEXT, code TEXT, owner_email TEXT,
  order_ref TEXT, created_at TEXT, kind TEXT);
CREATE TABLE affiliate_conversions (id INTEGER PRIMARY KEY, received_at TEXT, email TEXT,
  affiliate_slug TEXT);
"""


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.executescript(DDL)
    c.execute("INSERT INTO affiliate_signups VALUES (1,'alice@x.com','alice','approved')")
    c.execute("INSERT INTO affiliate_signups VALUES (2,'bob@x.com','bob','approved')")
    c.execute("INSERT INTO affiliate_signups VALUES (3,'carl@x.com','carl','pending')")
    c.commit()
    return c


def _inq(cx, email, slug, when):
    cx.execute("INSERT INTO inquiries VALUES (?,?,?,?)", (f"{email}{when}", when, email, slug))
    cx.commit()


def test_an_inquiry_carrying_a_ref_is_recoverable(cx):
    _inq(cx, "buyer@x.com", "alice", "2026-05-01")
    p = ab.plan(cx)
    assert p["counts"]["would_write"] == 1
    assert p["would_write"][0][:2] == ("buyer@x.com", "alice")


def test_first_touch_holds_across_the_whole_run(cx):
    """The property that matters. Two affiliates both have evidence for one person;
    the EARLIEST must win regardless of the order rows come back in."""
    _inq(cx, "buyer@x.com", "bob", "2026-08-01")     # later, inserted first
    _inq(cx, "buyer@x.com", "alice", "2026-05-01")   # earlier
    p = ab.plan(cx)
    assert p["counts"]["would_write"] == 1
    assert p["would_write"][0][1] == "alice", "the later affiliate took the referral"
    assert p["counts"]["lost_to_first_touch"] == 1


def test_someone_already_attributed_is_left_alone(cx):
    cx.execute("INSERT INTO referral_events (received_at,email,utm_source) "
               "VALUES ('2026-04-01','buyer@x.com','bob')")
    cx.commit()
    _inq(cx, "buyer@x.com", "alice", "2026-05-01")
    p = ab.plan(cx)
    assert p["counts"]["would_write"] == 0
    assert p["counts"]["already_attributed"] == 1


def test_an_unapproved_affiliate_is_never_credited(cx):
    _inq(cx, "buyer@x.com", "carl", "2026-05-01")
    assert ab.plan(cx)["counts"]["would_write"] == 0


def test_a_referral_code_resolves_its_owner_to_a_slug(cx):
    cx.execute("INSERT INTO referral_redemptions VALUES "
               "('buyer@x.com','CODE','bob@x.com','o1','2026-06-01','referral')")
    cx.commit()
    p = ab.plan(cx)
    assert p["would_write"][0][:2] == ("buyer@x.com", "bob")
    assert p["counts"]["by_source"] == {"referral-code": 1}


def test_a_dry_run_writes_nothing(monkeypatch, cx):
    _inq(cx, "buyer@x.com", "alice", "2026-05-01")
    import contextlib

    class _DB:
        @staticmethod
        @contextlib.contextmanager
        def connect(_p):
            yield cx

        @staticmethod
        def backend_of(_cx):
            return "sqlite"
    monkeypatch.setattr(ab, "db", _DB)
    res = ab.run(":memory:", dry_run=True)
    assert res["counts"]["written"] == 0
    assert cx.execute("SELECT COUNT(*) FROM referral_events").fetchone()[0] == 0
    assert res["counts"]["would_write"] == 1, "the preview must still say what it would do"


def test_a_real_run_writes_exactly_what_the_preview_promised(monkeypatch, cx):
    """Preview and act share one path, so they cannot drift."""
    _inq(cx, "buyer@x.com", "alice", "2026-05-01")
    _inq(cx, "other@x.com", "bob", "2026-05-02")
    import contextlib

    class _DB:
        @staticmethod
        @contextlib.contextmanager
        def connect(_p):
            yield cx

        @staticmethod
        def backend_of(_cx):
            return "sqlite"
    monkeypatch.setattr(ab, "db", _DB)
    preview = ab.run(":memory:", dry_run=True)["counts"]["would_write"]
    res = ab.run(":memory:", dry_run=False)
    assert res["counts"]["written"] == preview == 2
    rows = cx.execute("SELECT email, utm_source, utm_medium FROM referral_events "
                      "ORDER BY email").fetchall()
    assert rows == [("buyer@x.com", "alice", "backfill"),
                    ("other@x.com", "bob", "backfill")]


# ── the console route ──────────────────────────────────────────────────────────
# The route is the only way to run this against production, because the prod database
# is mounted on the web container alone. Its default must therefore be a preview: a
# route that wrote by default would turn a curious click into an irreversible act.

import re as _re
from pathlib import Path as _Path

_APP = _Path(__file__).resolve().parent.parent / "app.py"
_SRC = _APP.read_text()


def test_the_console_route_defaults_to_a_preview():
    body = _SRC[_SRC.index("def api_console_backfill_attribution"):]
    body = body[:body.index("@app.route", 10)]
    assert 'request.args.get("dry_run") or "1"' in body, (
        "the backfill route must default to dry_run; writing by default makes a stray "
        "click irreversible"
    )


def test_the_console_route_is_authenticated():
    body = _SRC[_SRC.index("def api_console_backfill_attribution"):]
    body = body[:body.index("@app.route", 10)]
    assert "_console_key_ok()" in body and "401" in body


def test_the_backfill_writes_no_reward_anywhere():
    """Attribution only. If a reward call ever appears in this module, a backfill would
    pay out on reconstructed history, which is far harder to undo than to skip."""
    src = (_APP.parent / "dashboard" / "attribution_backfill.py").read_text()
    for forbidden in ("credit(", "mark_rewarded", "rewarded_at", "points"):
        assert forbidden not in src, f"{forbidden} must not appear in the backfill"



def test_it_asks_the_backend_rather_than_trying_and_failing(monkeypatch, cx):
    """The bug that took this down in production, 2026-09-05.

    The first version tried sqlite_master and fell back to information_schema on the
    exception. On Postgres a failed statement ABORTS THE TRANSACTION, so that shape does
    not just fail its first query, it poisons the connection: every later command raises
    InFailedSqlTransaction. The 500 surfaced several calls further on than the line that
    actually broke, which is what made it confusing.

    These tests only ever ran against SQLite, so they could not have caught it. This one
    asserts the Postgres branch is chosen by ASKING, and that no sqlite catalogue is
    touched when the backend says postgres.
    """
    seen = []

    class _PgCx:
        def execute(self, sql, params=()):
            seen.append(sql)
            # Deliberately does NOT raise. The shape being guarded against catches
            # Exception, so raising here would be swallowed and the test would pass
            # against the very code it exists to reject. Record it and assert after.
            class _R:
                @staticmethod
                def fetchone():
                    return None

                @staticmethod
                def fetchall():
                    return []
            return _R()

    import contextlib

    class _DB:
        @staticmethod
        @contextlib.contextmanager
        def connect(_p):
            yield _PgCx()

        @staticmethod
        def backend_of(_cx):
            return "postgres"

    monkeypatch.setattr(ab, "db", _DB)
    ab.run(":memory:", dry_run=True)
    assert seen, "nothing was queried at all"
    assert not any("sqlite_master" in s for s in seen), (
        "queried sqlite_master on a postgres connection. On PG that aborts the "
        "transaction and poisons every later statement, which is how this returned a "
        "500 in production."
    )
    assert any("information_schema" in s for s in seen), (
        "the postgres branch was never taken"
    )
