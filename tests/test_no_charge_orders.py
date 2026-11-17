"""A $0 invoice can be settled as no charge (Glen, 2026-11-09).

Money's spec: money/handoffs/2026-11-09-no-charge-invoices.md. Glen: "to track who the
products went to, don't we need invoices even though they are $0?" A $0 order could never
become paid, because add_payment refuses a non-positive amount, and stock moves only for a
paid order. orders.mark_no_charge marks it paid by 'no charge' with no payment row.

It must take its bottles once, take nothing for bottles money already took by hand, earn
none of what money earns, and send nothing to anyone.
"""
import importlib
import smtplib
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import dispatch as D
from dashboard import orders as O
from dashboard import rbac as R
from dashboard import stock_counts as SC

CAT = {
    "lymph-flow": {"name": "Lymph Flow", "price_cents": 4500, "bottle_type": "90 caps"},
    "microbiome": {"name": "Microbiome", "price_cents": 4500, "bottle_type": "90 caps"},
    "biofield-analysis": {"name": "Biofield Analysis", "price_cents": 30000,
                          "service": True},
}
EMAIL = "gift@example.com"
REAL_FEE_FREE = O.credit_fee_free_on_payment


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    monkeypatch.delenv("STOCK_ALERT_EMAILS", raising=False)
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: path)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: CAT)
    monkeypatch.setattr(SC, "_send_one", lambda *a, **k: pytest.fail("stock alert sent"))
    SC._invalidate()
    calls = {"points": [], "referral": [], "membership": []}
    monkeypatch.setattr(O, "_points_settle_hook", lambda cx, o: calls["points"].append(o["id"]))
    monkeypatch.setattr(O, "_referral_settle_hook",
                        lambda cx, o: calls["referral"].append(o["id"]))
    monkeypatch.setattr(O, "_membership_grant_hook",
                        lambda cx, o: calls["membership"].append(o["id"]))
    fee_free = []
    real_fee_free = O.credit_fee_free_on_payment

    def _ff(cx, order, **kw):
        fee_free.append(order.get("id"))
        return real_fee_free(cx, order, **kw)
    monkeypatch.setattr(O, "credit_fee_free_on_payment", _ff)
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    O.init_orders_table(cx)
    SC.init_tables(cx)
    from dashboard import events as E
    E.init_event_tables(cx)
    from dashboard import db as DB
    if not DB.column_exists(cx, "orders", "portal_published"):      # prod has it
        cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER DEFAULT 0")
    cx.commit()
    yield {"cx": cx, "calls": calls, "fee_free": fee_free, "path": path}
    cx.close()
    SC._invalidate()


def _order(cx, items, *, total=0, ref="INH-GIFT", status="proposed", **extra):
    oid = O.upsert_order(cx, source="in-house", external_ref=ref, status=status,
                         email=EMAIL, name="Gift Client", items=items, total_cents=total)
    for k, v in extra.items():
        cx.execute(f"UPDATE orders SET {k}=? WHERE id=?", (v, oid))
    cx.commit()
    return oid


def _moves(cx, oid):
    return [tuple(r) for r in cx.execute(
        "SELECT slug, qty, taken FROM stock_order_moves WHERE order_id=? ORDER BY slug",
        (oid,)).fetchall()]


OWNER = R.Actor(role=R.OWNER, name="owner")


# ── refusals ───────────────────────────────────────────────────────────────────

def test_a_nonzero_total_is_refused(env):
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1, "unit_cents": 4500}], total=4500)
    with pytest.raises(ValueError, match=r"\$45.00"):
        O.mark_no_charge(cx, oid)
    o = O.get_order(cx, oid)
    assert o["pay_status"] != "paid" and o["status"] == "proposed"
    assert SC.get_count(cx, "lymph-flow") == 10


def test_cancelled_and_already_paid_are_refused(env):
    cx = env["cx"]
    a = _order(cx, [{"slug": "lymph-flow", "qty": 1}], ref="A", status="cancelled")
    b = _order(cx, [{"slug": "lymph-flow", "qty": 1}], ref="B", pay_status="paid",
               pay_method="Zelle")
    with pytest.raises(ValueError, match="cancelled"):
        O.mark_no_charge(cx, a)
    with pytest.raises(ValueError, match="already marked paid"):
        O.mark_no_charge(cx, b)
    assert O.get_order(cx, b)["pay_method"] == "Zelle"


def test_a_total_made_zero_by_credit_or_points_is_refused(env):
    """Those are paid with credit. Settling them as a gift would release the hold on the
    credit without spending it."""
    cx = env["cx"]
    a = _order(cx, [{"slug": "lymph-flow", "qty": 1}], ref="SC", ship_credit_applied_cents=900)
    b = _order(cx, [{"slug": "lymph-flow", "qty": 1}], ref="PT", points_redeemed_cents=4500)
    for oid in (a, b):
        with pytest.raises(ValueError, match="credit or points"):
            O.mark_no_charge(cx, oid)
        assert O.get_order(cx, oid)["pay_status"] != "paid"


def test_the_action_needs_the_owner_role(env):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    for role in (R.VA, R.OPS, R.AGENT, R.SYSTEM):
        res = D.dispatch_action(cx, "orders.mark_no_charge", {"order_id": oid},
                                R.Actor(role=role, name=role))
        assert res == {"status": "denied", "reason": "permission"}, role
    assert D.dispatch_action(cx, "orders.mark_no_charge", {"order_id": oid}, None)["status"] \
        == "denied"
    assert O.get_order(cx, oid)["pay_status"] != "paid"
    res = D.dispatch_action(cx, "orders.mark_no_charge", {"order_id": oid}, OWNER)
    assert res["status"] == "done", res
    assert O.get_order(cx, oid)["pay_status"] == "paid"


def test_the_route_refuses_without_a_key_and_rae_resolves_to_owner(monkeypatch):
    """No key: 401. Rae's per-user token resolves to the owner role, as it does for
    Record payment, so she can run it. Shaira's VA token cannot."""
    appmod = _app()
    monkeypatch.setattr(appmod, "_bos_actor", lambda: None)
    r = appmod.app.test_client().post("/api/action/orders.mark_no_charge",
                                      json={"order_id": 1})
    assert r.status_code == 401
    assert R.actor_for_scope("workspace:rae").role == R.OWNER
    assert R.actor_for_scope("workspace:shaira").role == R.VA


# ── stock ──────────────────────────────────────────────────────────────────────

def test_settling_takes_the_bottles_once(env):
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 2, "unit_cents": 0, "line_cents": 0},
                      {"slug": "biofield-analysis", "qty": 1, "unit_cents": 0,
                       "line_cents": 0, "service": True}])
    o, taken, changed, resumed = O.mark_no_charge(cx, oid)
    assert (taken, changed, resumed) == ([], [], False)
    assert (o["status"], o["pay_status"], o["pay_method"], o["paid_cents"]) == \
        ("new", "paid", "no charge", 0)
    assert SC.get_count(cx, "lymph-flow") == 8
    SC.sync_order(cx, oid)
    O.set_order_status(cx, oid, "done")
    assert SC.get_count(cx, "lymph-flow") == 8
    o, _t, _c, resumed = O.mark_no_charge(cx, oid)     # a repeat is a safe re-check
    assert resumed and o["status"] == "done"            # and moves nothing back
    assert SC.get_count(cx, "lymph-flow") == 8
    # No payment row is written: the ledger table is never touched.
    assert not cx.execute("SELECT 1 FROM sqlite_master WHERE name='order_payments'").fetchone()


def test_already_taken_writes_no_second_decrement(env):
    """Money lowered six counts by hand on 2026-11-09. set_count moved set_at to that
    day, so a sync of an order settled later WOULD take again without the marker row."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 39, who="money", reason="No charge to Rae, 1 bottle")
    SC.set_count(cx, "microbiome", 11, who="money", reason="No charge to K, 1 bottle")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1, "unit_cents": 0},
                      {"slug": "microbiome", "qty": 1, "unit_cents": 0}])
    res = D.dispatch_action(cx, "orders.mark_no_charge",
                            {"order_id": oid, "already_taken": ["lymph-flow"]}, OWNER)
    assert res["status"] == "done", res
    assert res["result"]["already_taken"] == ["lymph-flow"]
    # The row written for the hand-taken line: its qty, held as already taken.
    assert _moves(cx, oid) == [("lymph-flow", 1, 1), ("microbiome", 1, 1)]
    assert SC.get_count(cx, "lymph-flow") == 39          # not taken again
    assert SC.get_count(cx, "microbiome") == 10          # this one was not by hand
    for _ in range(2):                                   # later syncs take nothing
        SC.sync_order(cx, oid)
    SC.reconcile(cx)
    O.set_order_status(cx, oid, "packed")
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (39, 10)
    log = [(r["delta"], r["order_id"]) for r in SC.log_for(cx, "lymph-flow")]
    assert log == [(39, None)]                           # only money's hand set


def test_cancelling_returns_each_bottle_once_like_a_paid_order(env):
    """Decision: a cancel puts back what the order holds, as for any paid order. The
    hand-taken bottle was this gift's bottle, so cancelling the gift returns it, once.
    A reinstated order takes it again, once."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 39, who="money", reason="No charge, by hand")
    SC.set_count(cx, "microbiome", 11, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}, {"slug": "microbiome", "qty": 1}])
    O.mark_no_charge(cx, oid, already_taken="all")
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (39, 11)
    O.set_order_status(cx, oid, "cancelled")
    O.set_order_status(cx, oid, "cancelled")
    SC.reconcile(cx)
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (40, 12)
    O.set_order_status(cx, oid, "new")                   # reinstated
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (39, 11)


def test_a_shelf_count_after_the_gift_is_not_inflated_by_a_cancel(env):
    """A count set after the gift went out already reflects it, so a cancel then puts
    nothing back. That is the existing rule for any order sold before a count."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 39, who="money", reason="No charge, by hand")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    O.mark_no_charge(cx, oid, already_taken=True)
    SC.set_count(cx, "lymph-flow", 30, who="t", reason="shelf count")
    O.set_order_status(cx, oid, "cancelled")
    assert SC.get_count(cx, "lymph-flow") == 30


def test_already_taken_refuses_a_slug_not_on_the_order(env):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    with pytest.raises(ValueError, match="microbiom"):
        O.mark_no_charge(cx, oid, already_taken=["microbiom"])
    assert O.get_order(cx, oid)["pay_status"] != "paid"
    assert _moves(cx, oid) == []


# ── no money side effects, nothing sent ────────────────────────────────────────

def test_no_paid_order_hook_fires_and_nothing_is_sent(env, monkeypatch):
    cx = env["cx"]
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: sent.append(("smtp", a)))
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: sent.append(("smtp", a)))
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: sent.append(("gmail", a)))
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1},
                      {"slug": "biofield-analysis", "qty": 1, "service": True}])
    res = D.dispatch_action(cx, "orders.mark_no_charge", {"order_id": oid}, OWNER)
    assert res["status"] == "done", res
    assert env["calls"] == {"points": [], "referral": [], "membership": []}
    assert env["fee_free"] == []
    assert sent == []
    # A later caller that hands the order to the shared settlers still pays out nothing.
    o = O.get_order(cx, oid)
    O.settle_points_on_payment(cx, o)
    O.settle_referrals_on_payment(cx, o)
    assert env["calls"]["points"] == [] and env["calls"]["referral"] == []
    assert env["fee_free"] == []


def test_the_order_level_settlers_skip_a_no_charge_order(env, monkeypatch):
    """settle_order_points and the fee-free credit return before touching points."""
    from dashboard import points as P
    cx = env["cx"]
    touched = []
    monkeypatch.setattr(P, "init_points_table", lambda *a, **k: touched.append("points"))
    monkeypatch.setattr(O, "fee_free_paid_cents", lambda *a, **k: touched.append("fee") or 5000)
    o = {"id": 9, "email": EMAIL, "external_ref": "INH-X", "pay_method": "No Charge",
         "total_cents": 5000, "source": "in-house"}
    O.settle_order_points(cx, o)
    assert REAL_FEE_FREE(cx, o, action_method="Zelle", action_cents=5000) == 0
    assert touched == []
    # Control: the same order paid by Zelle reaches both.
    O.settle_order_points(cx, {**o, "pay_method": "Zelle"})
    REAL_FEE_FREE(cx, {**o, "pay_method": "Zelle"}, action_method="Zelle",
                  action_cents=5000)
    assert "points" in touched and "fee" in touched


def test_the_normal_paid_flow_is_unchanged(env):
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 2, "unit_cents": 4500}], total=9000)
    res = D.dispatch_action(cx, "orders.record_payment",
                            {"order_id": oid, "method": "Zelle", "amount_cents": 9000}, OWNER)
    assert res["status"] == "done", res
    o = O.get_order(cx, oid)
    assert (o["pay_status"], o["pay_method"], o["status"]) == ("paid", "Zelle", "new")
    assert SC.get_count(cx, "lymph-flow") == 8
    assert env["calls"]["membership"] == [oid] and env["calls"]["referral"] == [oid]
    assert oid in env["fee_free"]
    O.settle_points_on_payment(cx, o)
    assert env["calls"]["points"] == [oid]


# ── app-level settlers ─────────────────────────────────────────────────────────

def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    # An import failure fails the test. A skip here would pass these tests silently.
    return importlib.import_module("app")


def test_app_settlers_skip_a_no_charge_order(monkeypatch, tmp_path):
    appmod = _app()
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "a.db"))
    hit = []
    for name in ("_grant_membership_line_on_paid", "_grant_biofield_line_on_paid",
                 "_grant_supply_membership_on_paid", "_grant_waiting_supply_orders",
                 "_scan_reward_on_paid_order"):
        monkeypatch.setattr(appmod, name, lambda *a, _n=name, **k: hit.append(_n))
    monkeypatch.setattr(appmod, "_REFERRALS", True)
    from dashboard import points as P
    monkeypatch.setattr(P, "init_points_table", lambda *a, **k: hit.append("points"))
    gift = {"id": 5, "email": EMAIL, "external_ref": "INH-G", "source": "in-house",
            "pay_method": "no charge", "total_cents": 0, "points_redeemed_cents": 0,
            "items": [{"slug": "biofield-analysis", "qty": 1, "line_cents": 0},
                      {"slug": "lymph-flow", "qty": 1, "line_cents": 0}]}
    appmod._grant_membership_line_dep(gift)
    appmod._settle_referral(gift, order_ref="INH-G")
    assert appmod._settle_referrer_reward(None, gift, "INH-G") == 0
    appmod._settle_order_points(gift, order_ref="INH-G")
    assert hit == []
    # Control: the same order paid by Zelle reaches every grant.
    appmod._grant_membership_line_dep({**gift, "pay_method": "Zelle"})
    appmod._settle_referral({**gift, "pay_method": "Zelle"}, order_ref="INH-G")
    assert hit == ["_grant_membership_line_on_paid", "_grant_biofield_line_on_paid",
                   "_grant_supply_membership_on_paid", "_grant_waiting_supply_orders",
                   "_scan_reward_on_paid_order"]


def test_a_no_charge_biofield_anchors_no_supply_months(monkeypatch, tmp_path):
    appmod = _app()
    cx = sqlite3.connect(str(tmp_path / "s.db"))
    O.init_orders_table(cx)
    oid = O.upsert_order(cx, source="in-house", external_ref="BF", status="new",
                         email=EMAIL, items=[{"slug": "biofield-analysis", "qty": 1}],
                         total_cents=0)
    cx.execute("UPDATE orders SET pay_status='paid', pay_method='no charge', paid_at=? "
               "WHERE id=?", ("2026-11-09T00:00:00+00:00", oid))
    cx.commit()
    assert appmod._supply_cap_end(cx, EMAIL) is None
    cx.execute("UPDATE orders SET pay_method='Zelle' WHERE id=?", (oid,))
    cx.commit()
    assert appmod._supply_cap_end(cx, EMAIL) is not None


# ── /api/orders/manual accepts $0 lines ────────────────────────────────────────

@pytest.fixture
def manual(monkeypatch, tmp_path):
    appmod = _app()
    db = str(tmp_path / "m.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", CAT.get)
    appmod._init_people_table()
    from dashboard import db as DB
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        if not DB.column_exists(cx, "orders", "portal_published"):
            cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER DEFAULT 0")
        cx.commit()
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: type("A", (), {"role": appmod._bos_rbac.OWNER})())
    monkeypatch.setattr(appmod._shipping, "_default_db_path", lambda: db)
    with sqlite3.connect(db) as scx:
        appmod._shipping.init_shipping_schema(scx)
    holds = []
    monkeypatch.setattr(appmod, "_hold_new_order_and_invite",
                        lambda cx, oid: holds.append(oid))
    return appmod, db, holds


def _manual(appmod, lines, **extra):
    body = {"customer": {"name": "Gift Client", "email": EMAIL}, "pickup": True,
            "lines": lines}
    body.update(extra)
    return appmod.app.test_client().post("/api/orders/manual", json=body)


def test_a_zero_dollar_manual_order_with_a_biofield_line_is_accepted(manual):
    appmod, db, holds = manual
    r = _manual(appmod, [{"slug": "biofield-analysis", "qty": 1, "unit_cents": 0},
                         {"slug": "microbiome", "qty": 1, "unit_cents": 0}])
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["totals"]["total_cents"] == 0
    assert [(ln["slug"], ln["unit_cents"], ln["line_cents"]) for ln in j["lines"]] == [
        ("biofield-analysis", 0, 0), ("microbiome", 0, 0)]
    assert holds == []                       # a gift opens no household hold or invite
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        o = O.get_order(cx, j["order_id"])
    assert (o["status"], o["total_cents"]) == ("proposed", 0)
    assert o.get("pay_status") != "paid"


def test_a_normal_manual_order_still_prices_and_holds(manual):
    appmod, db, holds = manual
    r = _manual(appmod, [{"slug": "microbiome", "qty": 2}])
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["totals"]["total_cents"] == 9000
    assert holds == [j["order_id"]]


# ── round 2 review ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("col,val", [("ship_credit_applied_cents", 900),
                                     ("points_redeemed_cents", 4500),
                                     ("source", "groovekart")])
def test_a_change_after_the_read_is_not_overwritten(env, monkeypatch, col, val):
    """Credit, points or the source change between the read and the write. The
    guarded UPDATE refuses, so the order is not settled over the change."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    stale = O.get_order(cx, oid)
    cx.execute(f"UPDATE orders SET {col}=? WHERE id=?", (val, oid))
    cx.commit()
    real = O.get_order
    reads = []

    def _get(c, i):
        reads.append(i)
        return dict(stale) if len(reads) == 1 else real(c, i)
    monkeypatch.setattr(O, "get_order", _get)
    with pytest.raises(ValueError, match="changed while"):
        O.mark_no_charge(cx, oid)
    o = real(cx, oid)
    assert o["pay_status"] != "paid" and o.get("pay_method") in (None, "")
    assert SC.get_count(cx, "lymph-flow") == 10


def test_a_crash_after_the_settle_commits_is_healed_by_running_it_again(env, monkeypatch):
    """The stock sync commits on its own, so it cannot share the settle's transaction.
    A failure after the commit leaves a paid order with no bottles taken. Running the
    action again re-runs the receipt and the sync instead of refusing."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 39, who="money", reason="by hand")
    SC.set_count(cx, "microbiome", 11, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}, {"slug": "microbiome", "qty": 1}])
    real_receipt = O._ensure_portal_receipt

    def _boom(c, i):
        raise RuntimeError("process died after the commit")
    # The first step after the commit, so both the receipt and the sync are missed.
    monkeypatch.setattr(O, "_ensure_portal_receipt", _boom)
    res = D.dispatch_action(cx, "orders.mark_no_charge",
                            {"order_id": oid, "already_taken": ["lymph-flow"]}, OWNER)
    assert res["status"] == "failed" and "process died" in res["error"], res
    o = O.get_order(cx, oid)
    assert (o["pay_status"], o["pay_method"]) == ("paid", "no charge")
    assert SC.get_count(cx, "microbiome") == 11          # the sync never ran
    assert cx.execute("SELECT COALESCE(portal_published,0) FROM orders WHERE id=?",
                      (oid,)).fetchone()[0] == 0         # nor the receipt
    # The self-heal skips it: the hand-taken marker row counts as "synced".
    assert oid not in SC.reconcile(cx)
    monkeypatch.setattr(O, "_ensure_portal_receipt", real_receipt)
    res = D.dispatch_action(cx, "orders.mark_no_charge",
                            {"order_id": oid, "already_taken": ["lymph-flow"]}, OWNER)
    assert res["status"] == "done", res
    assert res["result"]["resumed"] is True
    assert res["result"]["marker_rows_changed"] == []
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (39, 10)
    D.dispatch_action(cx, "orders.mark_no_charge", {"order_id": oid}, OWNER)
    assert (SC.get_count(cx, "lymph-flow"), SC.get_count(cx, "microbiome")) == (39, 10)
    assert cx.execute("SELECT portal_published FROM orders WHERE id=?",
                      (oid,)).fetchone()[0] == 1


def test_a_repeat_on_an_order_paid_another_way_is_still_refused(env):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}], pay_status="paid", pay_method="cash")
    with pytest.raises(ValueError, match="already marked paid"):
        O.mark_no_charge(cx, oid)


def test_already_taken_raises_an_existing_row_with_less_taken(env):
    """An earlier sync left the line held but not taken (qty 1, taken 0), for example
    while its product had no count. DO NOTHING would keep taken at 0, and the next
    sync would take the hand-taken bottle a second time."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 39, who="money", reason="by hand")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    cx.execute("INSERT INTO stock_order_moves (order_id, slug, qty, taken, sold_at, "
               "updated_at) VALUES (?,?,?,?,?,?)",
               (oid, "lymph-flow", 1, 0, SC._now(), SC._now()))
    cx.commit()
    res = D.dispatch_action(cx, "orders.mark_no_charge",
                            {"order_id": oid, "already_taken": ["lymph-flow"]}, OWNER)
    assert res["status"] == "done", res
    assert res["result"]["marker_rows_changed"] == [
        {"slug": "lymph-flow", "before": {"qty": 1, "taken": 0},
         "after": {"qty": 1, "taken": 1}}]
    assert _moves(cx, oid) == [("lymph-flow", 1, 1)]
    SC.sync_order(cx, oid)
    assert SC.get_count(cx, "lymph-flow") == 39
    O.set_order_status(cx, oid, "cancelled")             # returned once, like any paid order
    assert SC.get_count(cx, "lymph-flow") == 40


# ── After #2293: a $0 order with bottles already taken waits for No charge ─────

@pytest.mark.parametrize("taken", [["microbiome"], True, "all", 1, "yes"])
def test_already_taken_on_create_skips_the_auto_pay(manual, taken):
    """Auto-pay would take a bottle money already took by hand (2026-11-16)."""
    appmod, db, holds = manual
    r = _manual(appmod, [{"slug": "microbiome", "qty": 1, "unit_cents": 0}],
                already_taken=taken)
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert (j["auto_paid"], j["settle_with_no_charge"]) == (False, True)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        o = O.get_order(cx, j["order_id"])
    assert o["status"] == "proposed" and o.get("pay_status") != "paid"


@pytest.mark.parametrize("taken", [None, [], "", False, "false", "FALSE", " No ", "none", 0, [""]])
def test_without_already_taken_a_bottle_order_is_auto_paid(manual, taken):
    appmod, db, holds = manual
    extra = {} if taken is None else {"already_taken": taken}
    r = _manual(appmod, [{"slug": "microbiome", "qty": 1, "unit_cents": 0}], **extra)
    j = r.get_json()
    assert (j["auto_paid"], j["settle_with_no_charge"]) == (True, False)
    with sqlite3.connect(db) as cx:
        cx.row_factory = sqlite3.Row
        assert O.get_order(cx, j["order_id"])["pay_method"] == "zero-total"


# ── Review rounds 1 and 2 ──────────────────────────────────────────────────────

def test_an_items_edit_after_the_read_is_not_settled_over(env, monkeypatch):
    """The markers are written from the items read. An edit landing after that read
    would leave them describing the wrong lines, so the guard refuses the settle."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    SC.set_count(cx, "microbiome", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    stale = O.get_order(cx, oid)
    stale_json = cx.execute("SELECT items_json FROM orders WHERE id=?", (oid,)).fetchone()[0]
    real = O.get_order

    def _get(c, i):
        return dict(stale)
    monkeypatch.setattr(O, "get_order", _get)
    # The editor lands between the item read and the guarded UPDATE.
    orig = cx.execute
    state = {"done": False}

    class _Cx:
        def __getattr__(self, n):
            return getattr(cx, n)

        def execute(self, sql, *a):
            out = orig(sql, *a)
            if "SELECT COALESCE(items_json" in sql and not state["done"]:
                state["done"] = True
                orig("UPDATE orders SET items_json=? WHERE id=?",
                     ('[{"slug": "microbiome", "qty": 2}]', oid))
            return out
    with pytest.raises(ValueError, match="changed while"):
        O.mark_no_charge(_Cx(), oid, already_taken=True)
    monkeypatch.setattr(O, "get_order", real)
    o = real(cx, oid)
    assert o["pay_status"] != "paid"
    assert _moves(cx, oid) == []
    assert SC.get_count(cx, "lymph-flow") == 10 and SC.get_count(cx, "microbiome") == 10
    assert stale_json != '[{"slug": "microbiome", "qty": 2}]'


def test_a_retry_after_a_cancel_returns_nothing_twice(env, monkeypatch):
    """A retry read the order as paid; a cancel lands before its write. The guard
    sees the cancel and refuses, so the markers are not raised again."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    O.mark_no_charge(cx, oid, already_taken=True)
    stale = O.get_order(cx, oid)
    O.set_order_status(cx, oid, "cancelled")
    after_cancel = SC.get_count(cx, "lymph-flow")
    real = O.get_order
    monkeypatch.setattr(O, "get_order",
                        lambda c, i: dict(stale) if i == oid else real(c, i))
    with pytest.raises(ValueError, match="changed while"):
        O.mark_no_charge(cx, oid, already_taken=True)
    monkeypatch.setattr(O, "get_order", real)
    assert SC.get_count(cx, "lymph-flow") == after_cancel


@pytest.mark.parametrize("method", ["no charge", "No Charge", " zero-total "])
def test_record_payment_refuses_a_free_method(env, method):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}], points_redeemed_cents=4500)
    with pytest.raises(ValueError, match="not a payment method"):
        O._record_payment_exec({"order_id": oid, "method": method}, {"cx": cx})
    assert O.get_order(cx, oid)["pay_status"] != "paid"


def test_the_flag_is_not_raised_for_an_order_no_charge_would_refuse(manual):
    appmod, db, holds = manual
    r = _manual(appmod, [{"slug": "microbiome", "qty": 1, "unit_cents": 4500}],
                already_taken=True)
    j = r.get_json()
    assert j["settle_with_no_charge"] is False


def test_a_retried_create_keeps_the_flag(manual):
    appmod, db, holds = manual
    lines = [{"slug": "microbiome", "qty": 1, "unit_cents": 0}]
    first = _manual(appmod, lines, already_taken=True, idempotency_key="k-1").get_json()
    again = _manual(appmod, lines, already_taken=True, idempotency_key="k-1").get_json()
    assert again.get("idempotent_repeat") is True, again
    assert first["settle_with_no_charge"] is True and again["settle_with_no_charge"] is True


def test_history_sql_ignores_case_and_spaces_in_the_gift_method():
    import re as _re
    src = Path(__file__).resolve().parents[1]
    for rel in ("app.py", "dashboard/scan_reward.py"):
        text = (src / rel).read_text()
        hits = _re.findall(r"(?i)(\S+)\s+NOT IN \('zero-total','no charge'\)", text)
        assert hits, rel
        assert all(h.lstrip("\"'(+ ").lower().startswith("lower(trim(") for h in hits), (rel, hits)


def test_a_retried_create_of_a_priced_order_does_not_raise_the_flag(manual):
    appmod, db, holds = manual
    lines = [{"slug": "microbiome", "qty": 1, "unit_cents": 4500}]
    _manual(appmod, lines, already_taken=True, idempotency_key="k-45")
    again = _manual(appmod, lines, already_taken=True, idempotency_key="k-45").get_json()
    assert again.get("idempotent_repeat") is True, again
    assert again["settle_with_no_charge"] is False


def test_a_resumed_retry_refuses_when_items_changed(env, monkeypatch):
    """A settled order whose items changed after the retry's read is not re-marked."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    O.mark_no_charge(cx, oid, already_taken=True)
    before = _moves(cx, oid)
    orig = cx.execute

    class _Cx:
        def __getattr__(self, n):
            return getattr(cx, n)

        def execute(self, sql, *a):
            out = orig(sql, *a)
            if "SELECT COALESCE(items_json" in sql:
                orig("UPDATE orders SET items_json=? WHERE id=?",
                     ('[{"slug": "lymph-flow", "qty": 3}]', oid))
            return out
    with pytest.raises(ValueError, match="changed while"):
        O.mark_no_charge(_Cx(), oid, already_taken=True)
    assert _moves(cx, oid) == before


def test_markers_come_from_the_pinned_read(env, monkeypatch):
    """An edit between get_order and the pinned read: the markers follow the pinned
    lines, never the stale ones, so no removed line is put back on the shelf."""
    cx = env["cx"]
    SC.set_count(cx, "lymph-flow", 10, who="t", reason="shelf")
    SC.set_count(cx, "microbiome", 10, who="t", reason="shelf")
    oid = _order(cx, [{"slug": "lymph-flow", "qty": 1}])
    stale = O.get_order(cx, oid)
    cx.execute("UPDATE orders SET items_json=? WHERE id=?",
               ('[{"slug": "microbiome", "qty": 2}]', oid))
    cx.commit()
    real = O.get_order
    reads = []

    def _get(c, i):
        reads.append(i)
        return dict(stale) if len(reads) == 1 else real(c, i)
    monkeypatch.setattr(O, "get_order", _get)
    O.mark_no_charge(cx, oid, already_taken=True)
    assert _moves(cx, oid) == [("microbiome", 2, 2)]
    assert SC.get_count(cx, "lymph-flow") == 10 and SC.get_count(cx, "microbiome") == 10
