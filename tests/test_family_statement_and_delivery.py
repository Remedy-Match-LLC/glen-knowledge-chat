"""A $0 order leaves the shelf on its delivered date, and a household gets one
statement a month (Glen and Rae, 2026-11-22, for the Perdomo family).

Glen ruled "all": every $0 order can carry a delivered date. With none it leaves the
shelf when it is paid, as before. The statement lists each paid delivery to a member
of the household in that month, by the delivered date or the Hilo day it was paid.
"""
import json
import sqlite3

import pytest

from dashboard import family_statement as FS
from dashboard import orders as O
from dashboard import stock_counts as SC

CAT = {"scar-silk": {"name": "Scar Silk", "price_cents": 7000},
       "moisturize": {"name": "Moisturize", "price_cents": 7000}}
TODAY = "2026-11-22"


@pytest.fixture
def cx(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    monkeypatch.setenv("STOCK_ALERT_EMAILS", "0")
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: path)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: CAT)
    monkeypatch.setattr(SC, "_send_one", lambda *a: True)
    monkeypatch.setattr(SC, "_spawn", lambda fn: fn())
    monkeypatch.setattr(SC, "hst_today", lambda: TODAY)
    # Counts are stamped on TODAY, so the dates below stay in the future for good.
    monkeypatch.setattr(SC, "_now", lambda: TODAY + "T12:00:00+00:00")
    SC._invalidate()
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    O.init_orders_table(c)
    SC.init_tables(c)
    yield c
    c.close()
    SC._invalidate()


def _free(cx, ref, qty=2, slug="scar-silk", person_id=None):
    oid = O.upsert_order(cx, source="in-house", external_ref=ref, email="f@x.com",
                         items=[{"slug": slug, "name": CAT[slug]["name"], "qty": qty}],
                         total_cents=0)
    if person_id is not None:
        cx.execute("UPDATE orders SET person_id=? WHERE id=?", (person_id, oid))
        cx.commit()
    return oid


def _count(cx, slug="scar-silk"):
    return SC.get_count(cx, slug)


# ── stock on the delivered date ───────────────────────────────────────────────

def test_a_free_order_with_no_date_leaves_when_settled_as_before(cx):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 8


def test_a_date_on_bottles_already_out_moves_nothing(cx, monkeypatch):
    """Once its bottles have left, a date only labels the statement (review round 3)."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 8
    O.set_delivered_on(cx, oid, "2026-11-25")
    assert _count(cx) == 8
    O.set_delivered_on(cx, oid, "")
    O.set_delivered_on(cx, oid, "2026-11-02")
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-25")
    SC.sync_due_deliveries(cx)
    SC.reconcile(cx)
    assert _count(cx) == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 10


def test_held_bottles_go_out_once_on_their_day(cx, monkeypatch):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-25")
    O.mark_no_charge(cx, oid)
    SC.sync_due_deliveries(cx)                       # not yet due: nothing moves
    assert _count(cx) == 10
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-25")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-26")
    SC.sync_due_deliveries(cx)                       # repeating moves nothing
    SC.reconcile(cx)
    O.set_delivered_on(cx, oid, "2026-12-09")        # out already: date only
    assert _count(cx) == 8
    O.set_order_status(cx, oid, "cancelled")         # taken from this count: back once
    assert _count(cx) == 10
    SC.reconcile(cx)
    assert _count(cx) == 10


def test_a_date_set_before_settling_holds_the_bottles(cx):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A", qty=3)
    O.set_delivered_on(cx, oid, "2026-12-01")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 10
    SC.reconcile(cx)
    assert _count(cx) == 10


def test_the_hourly_job_takes_a_delivery_dated_today(cx, monkeypatch):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-23")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 10
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-23")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8


def test_clearing_the_date_takes_the_bottles_now(cx):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-12-01")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 10
    O.set_delivered_on(cx, oid, "")
    assert _count(cx) == 8
    assert O.get_order(cx, oid)["delivered_on"] in (None, "")


def test_a_paid_order_cannot_take_a_delivered_date(cx):
    oid = O.upsert_order(cx, source="in-house", external_ref="P", email="p@x.com",
                         items=[{"slug": "scar-silk", "qty": 1}], total_cents=7000)
    with pytest.raises(ValueError, match="not a \\$0 order"):
        O.set_delivered_on(cx, oid, "2026-11-25")


@pytest.mark.parametrize("bad", ["2026-13-01", "25 Nov", "2026-02-30"])
def test_a_bad_date_is_refused(cx, bad):
    oid = _free(cx, "A")
    with pytest.raises(ValueError):
        O.set_delivered_on(cx, oid, bad)


def test_a_cancelled_dated_order_gives_nothing_back_twice(cx):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.mark_no_charge(cx, oid)
    O.set_delivered_on(cx, oid, "2026-11-20")
    assert _count(cx) == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 10
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 10


# ── the family statement ──────────────────────────────────────────────────────

def _family(cx):
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, first_name TEXT, "
               "last_name TEXT, email TEXT, tags TEXT)")
    cx.execute("CREATE TABLE households (id INTEGER PRIMARY KEY, slug TEXT, name TEXT)")
    cx.execute("INSERT INTO households (slug, name) VALUES ('perdomo', 'Perdomo family')")
    for pid, first, tags in ((1, "Kai", ["household:perdomo"]),
                             (2, "Lani", ["household:perdomo", "member"]),
                             (3, "Other", ["household:perdomo-west"])):
        cx.execute("INSERT INTO people VALUES (?,?,?,?,?)",
                   (pid, first, "Perdomo", f"{pid}@x.com", json.dumps(tags)))
    cx.commit()


def test_the_statement_lists_each_members_deliveries_in_the_month(cx):
    _family(cx)
    a = _free(cx, "A", qty=2, person_id=1)
    b = _free(cx, "B", qty=1, slug="moisturize", person_id=2)
    other = _free(cx, "C", person_id=3)                # a different household
    unpaid = _free(cx, "D", person_id=1)               # never settled
    for oid in (a, b, other):
        O.mark_no_charge(cx, oid)
    O.set_delivered_on(cx, a, "2026-11-03")
    O.set_delivered_on(cx, b, "2026-11-18")
    late = _free(cx, "E", person_id=2)
    O.mark_no_charge(cx, late)
    O.set_delivered_on(cx, late, "2026-12-02")         # next month's statement
    st = FS.build(cx, "perdomo", "2026-11")
    assert [m["id"] for m in st["members"]] == [1, 2]
    assert [(d["order_id"], d["date"], d["member"]) for d in st["deliveries"]] == [
        (a, "2026-11-03", "Kai Perdomo"), (b, "2026-11-18", "Lani Perdomo")]
    assert st["deliveries"][0]["items"] == [{"name": "Scar Silk", "qty": 2}]
    assert st["total_cents"] == 0
    assert unpaid not in [d["order_id"] for d in st["deliveries"]]
    assert [d["order_id"] for d in FS.build(cx, "perdomo", "2026-12")["deliveries"]] == [late]


def test_an_undated_delivery_counts_on_the_hilo_day_it_was_paid(cx):
    _family(cx)
    oid = _free(cx, "A", person_id=1)
    O.mark_no_charge(cx, oid)
    # 05:00 UTC on 1 December is still 30 November in Hilo.
    cx.execute("UPDATE orders SET paid_at='2026-12-01T05:00:00+00:00' WHERE id=?", (oid,))
    cx.commit()
    assert [d["date"] for d in FS.build(cx, "perdomo", "2026-11")["deliveries"]] == [
        "2026-11-30"]
    assert FS.build(cx, "perdomo", "2026-12")["deliveries"] == []


def test_a_cancelled_order_is_not_on_the_statement(cx):
    _family(cx)
    oid = _free(cx, "A", person_id=1)
    O.mark_no_charge(cx, oid)
    O.set_delivered_on(cx, oid, "2026-11-05")
    O.set_order_status(cx, oid, "cancelled")
    assert FS.build(cx, "perdomo", "2026-11")["deliveries"] == []


@pytest.mark.parametrize("month", ["2026-13", "Nov", "", "2026-1"])
def test_a_bad_month_is_refused(cx, month):
    _family(cx)
    with pytest.raises(ValueError):
        FS.build(cx, "perdomo", month)


def test_an_unknown_household_is_refused(cx):
    _family(cx)
    with pytest.raises(ValueError):
        FS.build(cx, "nobody", "2026-11")


def test_a_recount_before_the_delivery_date_still_gives_the_bottles_out(cx, monkeypatch):
    """The bottles were on the shelf when Rae counted, so the delivery takes from it."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-25")
    O.mark_no_charge(cx, oid)
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-24T12:00:00+00:00")
    SC.set_count(cx, "scar-silk", 9, who="t", reason="recount")
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-25")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 7


def test_a_recount_late_on_the_day_before_does_not_hide_the_delivery(cx, monkeypatch):
    """08:00 UTC on the 25th is still the 24th in Hilo (review round 2)."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-25")
    O.mark_no_charge(cx, oid)
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-25T08:00:00+00:00")
    SC.set_count(cx, "scar-silk", 9, who="t", reason="recount")
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-25")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 7


def test_the_hourly_job_heals_a_date_change_whose_sync_failed(cx, monkeypatch):
    """The date commits before its stock sync; a crash between them is healed."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-12-10")
    O.mark_no_charge(cx, oid)
    assert _count(cx) == 10
    monkeypatch.setattr(O, "_stock_sync", lambda *a: None)    # the sync never ran
    O.set_delivered_on(cx, oid, "")                           # cleared: due now
    assert _count(cx) == 10
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8


def test_a_long_overdue_delivery_is_still_taken(cx, monkeypatch):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-25")
    O.mark_no_charge(cx, oid)
    monkeypatch.setattr(SC, "hst_today", lambda: "2027-03-01")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8


def test_a_timestamp_with_no_zone_is_read_as_utc(cx):
    assert FS._hst_day("2026-12-01 05:00:00") == "2026-11-30"


def test_held_bottles_cleared_after_a_recount_still_go_out(cx, monkeypatch):
    """They sat on the shelf through the recount, so the recount counted them."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    monkeypatch.setattr(O, "_now", lambda: "2026-11-22T12:30:00+00:00")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-30")
    O.mark_no_charge(cx, oid)
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-22T20:00:00+00:00")
    SC.set_count(cx, "scar-silk", 10, who="t", reason="recount")
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-22T21:00:00+00:00")
    O.set_delivered_on(cx, oid, "")
    assert _count(cx) == 8
    SC.sync_due_deliveries(cx)
    SC.reconcile(cx)
    assert _count(cx) == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 10


def test_a_held_order_cancelled_before_its_day_gives_nothing_back(cx, monkeypatch):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-30")
    O.mark_no_charge(cx, oid)
    O.set_order_status(cx, oid, "cancelled")
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-12-01")
    SC.sync_due_deliveries(cx)
    SC.reconcile(cx)
    assert _count(cx) == 10


def test_an_order_paid_with_credit_takes_no_date(cx):
    oid = _free(cx, "A")
    cx.execute("UPDATE orders SET ship_credit_applied_cents=500 WHERE id=?", (oid,))
    cx.commit()
    with pytest.raises(ValueError, match="credit or points"):
        O.set_delivered_on(cx, oid, "2026-11-25")


def test_a_superseded_order_is_not_on_the_statement_twice(cx):
    _family(cx)
    old = _free(cx, "A", person_id=1)
    new = _free(cx, "B", person_id=1)
    for oid in (old, new):
        O.mark_no_charge(cx, oid)
        O.set_delivered_on(cx, oid, "2026-11-05")
    cx.execute("UPDATE orders SET superseded_by_order_id=? WHERE id=?", (new, old))
    cx.commit()
    assert [d["order_id"] for d in FS.build(cx, "perdomo", "2026-11")["deliveries"]] == [new]


def test_a_recount_after_held_bottles_go_out_absorbs_them(cx, monkeypatch):
    """Released bottles are an ordinary sale: a later recount counts them as gone."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.set_delivered_on(cx, oid, "2026-11-25")
    O.mark_no_charge(cx, oid)
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-25")
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-25T20:00:00+00:00")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8
    assert cx.execute("SELECT COUNT(*) FROM stock_order_moves WHERE order_id=? AND slug=?",
                      (oid, SC.HELD_MARK)).fetchone()[0] == 0     # the hold is gone
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-26T20:00:00+00:00")
    SC.set_count(cx, "scar-silk", 8, who="t", reason="recount")
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 8


def test_an_item_added_to_an_order_already_out_goes_out_now(cx, monkeypatch):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    SC.set_count(cx, "moisturize", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.mark_no_charge(cx, oid)
    O.set_delivered_on(cx, oid, "2026-11-30")
    cx.execute("UPDATE orders SET items_json=? WHERE id=?", (json.dumps(
        [{"slug": "scar-silk", "qty": 2}, {"slug": "moisturize", "qty": 1}]), oid))
    cx.commit()
    SC.sync_order(cx, oid)
    assert (_count(cx), _count(cx, "moisturize")) == (8, 9)


def test_a_release_after_cancel_and_reinstate_still_returns_on_cancel(cx, monkeypatch):
    """Review: an older empty row must not make a released order look pre-recount."""
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _free(cx, "A")
    O.mark_no_charge(cx, oid)
    O.set_delivered_on(cx, oid, "2026-11-30")
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 10
    O.set_order_status(cx, oid, "new")                        # reinstated: held again
    assert _count(cx) == 10
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-29T20:00:00+00:00")
    SC.set_count(cx, "scar-silk", 10, who="t", reason="recount")
    monkeypatch.setattr(SC, "_now", lambda: "2026-11-30T20:00:00+00:00")
    monkeypatch.setattr(SC, "hst_today", lambda: "2026-11-30")
    SC.sync_due_deliveries(cx)
    assert _count(cx) == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx) == 10


def test_the_hourly_job_touches_only_held_orders(cx):
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    out = _free(cx, "A")
    O.mark_no_charge(cx, out)
    held = _free(cx, "B")
    O.set_delivered_on(cx, held, "2026-12-05")
    O.mark_no_charge(cx, held)
    assert SC._dated_delivery_ids(cx) == [held]
