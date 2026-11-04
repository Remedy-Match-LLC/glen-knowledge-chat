"""Over $200 paid in 30 days opens Group Coaching and the invitation (Glen, 2026-11-04).

Glen, primary tab: "extend invitation to anyone who purchases over $200 in the last 30
days, independent of biofield status at least for now." Readings confirmed in the
platform tab ("yes"): Group Coaching access too; money paid on non-cancelled orders over a
rolling 30 days; ends when it drops to $200 or less; no member pricing.

Seeded through dashboard.orders' own table into a fresh database.
"""
import importlib
import sqlite3
from datetime import datetime, timedelta

import pytest

import app as appmod
from dashboard import orders as O
from dashboard import spend_access as SA
from dashboard import subscriptions as subs

NOW = datetime(2026, 11, 4, 18, 0, 0)
SECRET = "spend-access-secret"


def _order(cx, email, cents, days_ago, status="done", paid=True):
    O.init_orders_table(cx)
    when = (NOW - timedelta(days=days_ago)).isoformat()
    cx.execute("INSERT INTO orders (source, external_ref, email, status, total_cents, paid_cents, "
               "paid_at, created_at, updated_at, items_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("in-house", f"t-{email}-{cents}-{days_ago}-{status}-{paid}", email, status, cents, cents if paid else 0, when if paid else None,
                when, when, "[]"))


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    SA.ensure_tables(c)
    return c


def test_over_200_in_the_window_qualifies(cx):
    _order(cx, "a@example.com", 15000, 3)
    _order(cx, "A@example.com", 5100, 20)      # same buyer, any case: 201.00
    assert SA.qualifies(cx, "a@example.com", now=NOW)
    assert SA.spenders(cx, now=NOW) == {"a@example.com": 20100}


def test_exactly_200_does_not(cx):
    _order(cx, "b@example.com", 20000, 1)
    assert not SA.qualifies(cx, "b@example.com", now=NOW)


def test_an_order_past_30_days_drops_out(cx):
    _order(cx, "c@example.com", 15000, 2)
    _order(cx, "c@example.com", 15000, 31)
    assert not SA.qualifies(cx, "c@example.com", now=NOW)
    assert SA.qualifies(cx, "c@example.com", now=NOW - timedelta(days=2))


def test_cancelled_and_unpaid_orders_do_not_count(cx):
    _order(cx, "d@example.com", 30000, 1, status="cancelled")
    _order(cx, "d@example.com", 30000, 1, paid=False)
    assert not SA.qualifies(cx, "d@example.com", now=NOW)


def test_money_paid_counts_not_the_invoice_total(cx):
    _order(cx, "e@example.com", 25000, 1)
    cx.execute("UPDATE orders SET total_cents=50000, paid_cents=15000 WHERE email=?",
               ("e@example.com",))
    assert not SA.qualifies(cx, "e@example.com", now=NOW)


def test_a_zoned_paid_at_is_read(cx):
    _order(cx, "f@example.com", 30000, 1)
    cx.execute("UPDATE orders SET paid_at=? WHERE email=?",
               ((NOW - timedelta(days=1)).isoformat() + "Z", "f@example.com"))
    assert SA.qualifies(cx, "f@example.com", now=NOW)


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    path = str(tmp_path / "chat_log.db")
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        subs.init_subscriptions_table(c)
        for fn in ("migrate_add_failed_count", "migrate_add_membership_columns",
                   "migrate_add_term_cap_column", "migrate_add_attribution_column",
                   "migrate_add_consent_column"):
            getattr(subs, fn)(c)
        now = datetime.utcnow()
        SA.ensure_tables(c)
        for email, cents in (("spender@example.com", 26300), ("small@example.com", 9300)):
            when = (now - timedelta(days=2)).isoformat()
            c.execute("INSERT INTO orders (source, external_ref, email, status, total_cents, paid_cents, "
                      "paid_at, created_at, updated_at, items_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("in-house", f"t-{email}", email, "done", cents, cents, when, when, when, "[]"))
        appmod.init_membership_tables(c)
        c.commit()
    monkeypatch.setattr(appmod, "LOG_DB", path)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: False)
    monkeypatch.setattr(appmod, "_is_certification_student", lambda e: False)
    return path


def test_the_coaching_gate_opens_for_a_spender_only(live_db):
    assert appmod._group_coaching_entitled("spender@example.com")
    assert not appmod._group_coaching_entitled("small@example.com")


def test_a_spender_gets_no_member_pricing(live_db, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(appmod, "LOG_DB", live_db)
    assert not appmod._is_paid_member("spender@example.com")


def test_the_board_lists_spenders_in_their_own_bucket(live_db):
    r = appmod.app.test_client().get(f"/api/console/members?key={SECRET}")
    assert r.status_code == 200, r.data
    b = r.get_json()["buckets"]
    assert [row["email"] for row in b["spend"]] == ["spender@example.com"]
    others = {row["email"] for k, rows in b.items() if k != "spend" for row in rows}
    assert "spender@example.com" not in others


def test_a_member_already_on_the_board_is_not_listed_again(live_db, monkeypatch):
    from dashboard import subscriptions as S2
    real = S2.list_membership_holders
    monkeypatch.setattr(S2, "list_membership_holders",
                        lambda cx: list(real(cx)) + [{"email": "Spender@Example.com",
                                                      "source": "paid"}])
    with sqlite3.connect(live_db) as c:
        b = appmod.members_board_buckets(c)
    assert b["spend"] == []


def test_a_failed_spend_read_fails_the_strict_board(live_db, monkeypatch):
    monkeypatch.setattr(SA, "spenders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with sqlite3.connect(live_db) as c, pytest.raises(RuntimeError):
        appmod.members_board_buckets(c, strict=True)
    with sqlite3.connect(live_db) as c:
        assert appmod.members_board_buckets(c)["spend"] == []


def test_a_refund_is_taken_off(cx):
    _order(cx, "g@example.com", 30000, 1)
    oid = cx.execute("SELECT id FROM orders WHERE email='g@example.com'").fetchone()[0]
    now = NOW.isoformat()
    cx.execute("INSERT INTO order_payments (order_id, kind, amount_cents, status, created_at) "
               "VALUES (?,?,?,?,?)", (oid, "refund", 15000, "active", now))
    cx.execute("INSERT INTO order_payments (order_id, kind, amount_cents, status, created_at) "
               "VALUES (?,?,?,?,?)", (oid, "refund", 5000, "void", now))
    assert SA.spend_by_email(cx, now=NOW) == {"g@example.com": 15000}
    assert not SA.qualifies(cx, "g@example.com", now=NOW)


def test_a_refund_before_full_payment_is_not_taken_off_twice(cx):
    # paid_cents is written once at full payment, already net of an earlier refund.
    _order(cx, "h@example.com", 25000, 1)
    oid = cx.execute("SELECT id FROM orders WHERE email='h@example.com'").fetchone()[0]
    before = (NOW - timedelta(days=2)).isoformat()
    cx.execute("INSERT INTO order_payments (order_id, kind, amount_cents, status, created_at) "
               "VALUES (?,?,?,?,?)", (oid, "refund", 5000, "active", before))
    assert SA.spend_by_email(cx, now=NOW) == {"h@example.com": 25000}


def test_exactly_200_is_not_on_the_spenders_list(cx):
    _order(cx, "i@example.com", 20000, 1)
    _order(cx, "j@example.com", 20001, 1)
    assert SA.spenders(cx, now=NOW) == {"j@example.com": 20001}


def test_day_29_counts_and_a_future_date_does_not(cx):
    _order(cx, "k@example.com", 25000, 29.5)
    _order(cx, "l@example.com", 25000, -1)
    assert SA.spenders(cx, now=NOW) == {"k@example.com": 25000}


def test_the_gate_never_runs_table_setup(live_db, monkeypatch):
    monkeypatch.setattr(SA, "ensure_tables",
                        lambda cx: (_ for _ in ()).throw(AssertionError("setup ran")))
    assert appmod._group_coaching_entitled("spender@example.com")


def test_the_sender_tells_a_spender_coaching_is_included(live_db, monkeypatch):
    weekly = importlib.import_module("scripts.weekly_live_invitation")
    monkeypatch.setattr(weekly, "_authoritative_access_sets",
                        weekly._authoritative_access_sets)
    import types, sys
    fake = types.ModuleType("db_supabase")

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): pass
        def fetchall(self): return []
    fake.supabase_cursor = lambda: _Cur()
    monkeypatch.setitem(sys.modules, "db_supabase", fake)
    paid, _cert = weekly._authoritative_access_sets()
    # Not in the paid set: an unsubscribe still drops a spender (review round 1).
    assert "spender@example.com" not in paid
    assert weekly._spend_set() == {"spender@example.com"}


def test_an_unsubscribed_spender_stays_suppressed():
    weekly = importlib.import_module("scripts.weekly_live_invitation")
    src_run = weekly.run.__code__.co_names
    assert "_spend_set" in src_run
    import inspect
    body = inspect.getsource(weekly.run)
    assert "_suppressed_for_invitation(cx, email, waived)" in body
    assert "eligible = waived | _spend_set()" in body
    assert "eligible" not in body.split("_suppressed_for_invitation(cx, email, ")[1][:10]
    corr = inspect.getsource(weekly.run_correction)
    assert corr.count("_suppressed_for_invitation(cx, e, waived)") == 1
    assert corr.count("_suppressed_for_invitation(cx, email, waived)") == 1
    assert "eligible = waived | _spend_set()" in corr


def test_the_waiver_set_skips_only_paid_and_certified(monkeypatch):
    weekly = importlib.import_module("scripts.weekly_live_invitation")
    from dashboard import email_suppression as ES
    monkeypatch.setattr(ES, "suppression_reason", lambda cx, e: ES.UNSUBSCRIBED_TAG)
    assert weekly._suppressed_for_invitation(None, "spender@example.com", {"paid@example.com"})
    assert not weekly._suppressed_for_invitation(None, "paid@example.com", {"paid@example.com"})


def test_spend_under_a_merged_address_counts_for_the_survivor(cx):
    from dashboard import person_aliases as PA
    PA.init_tables(cx)
    PA.add_alias(cx, "old@example.com", "new@example.com", 1)
    _order(cx, "old@example.com", 15000, 3)
    _order(cx, "new@example.com", 6000, 2)
    assert SA.spenders(cx, now=NOW) == {"new@example.com": 21000}
    assert SA.qualifies(cx, "old@example.com", now=NOW)
    assert SA.qualifies(cx, "new@example.com", now=NOW)
