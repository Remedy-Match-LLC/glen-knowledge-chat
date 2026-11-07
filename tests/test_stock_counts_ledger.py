"""Shelf counts move with orders, once (Glen, 2026-11-06).

Plan: platform/plans/2026-11-06-stock-counts-in-the-app.md. A paid order takes its
bottles off. A cancelled order puts them back, and a reinstated one takes them again.
Bundles take from each component. GrooveKart orders come off on arrival and an unmatched
name never breaks the webhook. Average monthly sales combine every order source and count
an overlap once. Glen and Rae are alerted once per crossing, and a failed send is
retried. A crash between payment and the stock move heals itself. Nothing moves with
STOCK_COUNTS_ENABLED off.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import orders as O
from dashboard import purchase_history as PH
from dashboard import stock_counts as SC

CAT = {
    "scar-silk": {"name": "Scar Silk", "price_cents": 7000},
    "moisturize": {"name": "Moisturize", "price_cents": 7000},
    "wholomega": {"name": "WholOmega", "price_cents": 7000},
    "made-one": {"name": "Made One", "price_cents": 7000, "made_to_order": True},
    "old-silk": {"name": "Old Silk", "inactive": True, "superseded_by": "scar-silk"},
    "skin-bundle": {"name": "Skin Bundle", "bundle": True, "price_cents": 14000,
                    "bundle_component_slugs": [{"slug": "scar-silk", "qty": 2},
                                               {"slug": "moisturize", "qty": 1}]},
}
REAL_SEND_ONE = SC._send_one
GLEN = "drglenswartwout@gmail.com"
RAE = "suerae1111@gmail.com"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    monkeypatch.setenv("STOCK_ALERT_EMAILS", "1")         # the mail path stays tested
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: path)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: CAT)
    sent, fail = [], set()

    def _send(to, subject, body):
        if to in fail:
            return False
        sent.append((to, subject))
        return True
    monkeypatch.setattr(SC, "_send_one", _send)
    monkeypatch.setattr(SC, "_spawn", lambda fn: fn())     # deliver inline
    SC._invalidate()
    cx = sqlite3.connect(path)
    O.init_orders_table(cx)
    SC.init_tables(cx)
    yield {"cx": cx, "sent": sent, "fail": fail, "path": path}
    cx.close()
    SC._invalidate()


def _subjects(sent, to=GLEN):
    return [s for t, s in sent if t == to]


def _order(cx, items, ref="R1", source="funnel", email="a@x.com"):
    return O.upsert_order(cx, source=source, external_ref=ref, email=email, items=items,
                          total_cents=7000)


def _count(cx, slug):
    return SC.get_count(cx, slug)


def _set(cx, slug, n, reason="shelf count"):
    return SC.set_count(cx, slug, n, who="t", reason=reason)


def test_a_paid_order_decrements_once_even_if_marked_paid_twice(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    assert _count(cx, "scar-silk") == 10            # unpaid: nothing leaves
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 7
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.mark_order_paid_keep_status(cx, oid, method="card", amount_cents=7000)
    O.set_order_status(cx, oid, "shipped")
    assert _count(cx, "scar-silk") == 7
    log = SC.log_for(cx, "scar-silk")
    assert [(r["delta"], r["new_count"], r["order_id"]) for r in log] == [
        (-3, 7, oid), (10, 10, None)]
    assert log[0]["who"] == "order" and log[1]["reason"] == "shelf count"


def test_mark_paid_keep_status_decrements_too(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 5)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}])
    O.mark_order_paid_keep_status(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 4


def test_cancel_restores_and_a_reinstated_order_takes_again(env):
    """Review rounds 1 and 2: paid, cancelled, then live again left the bottles back."""
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 4}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 6
    O.set_order_status(cx, oid, "cancelled")
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)    # paid again
    assert _count(cx, "scar-silk") == 6
    O.set_order_status(cx, oid, "cancelled")
    O.set_order_status(cx, oid, "shipped")                            # reinstated
    assert _count(cx, "scar-silk") == 6
    O.set_order_status(cx, oid, "done")
    assert _count(cx, "scar-silk") == 6


def test_a_cancel_that_commits_first_leaves_no_decrement(env):
    """Review: the pay hook must read the order's CURRENT status."""
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 4}])
    # The payment commits, then a cancel commits, and only then the pay hook runs.
    cx.execute("UPDATE orders SET pay_status='paid', paid_at=? WHERE id=?", (SC._now(), oid))
    cx.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    cx.commit()
    SC.sync_order(cx, oid)
    assert _count(cx, "scar-silk") == 10
    # A payment recorded on the cancelled order, keeping its status, takes nothing.
    O.mark_order_paid_keep_status(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 10


def test_cancelling_an_unpaid_order_puts_nothing_back(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 4}])
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10


def test_a_bundle_decrements_each_component(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    _set(cx, "moisturize", 10)
    oid = _order(cx, [{"slug": "skin-bundle", "qty": 2}, {"slug": "scar-silk", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=14000)
    assert _count(cx, "scar-silk") == 5             # 2 bundles x 2 + 1 loose
    assert _count(cx, "moisturize") == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10 and _count(cx, "moisturize") == 10


def test_an_edited_paid_order_moves_only_the_difference(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.set_order_items_and_total(cx, oid, [{"slug": "scar-silk", "qty": 5}], 7000)
    assert _count(cx, "scar-silk") == 5
    O.set_order_items_and_total(cx, oid, [{"slug": "scar-silk", "qty": 1}], 7000)
    assert _count(cx, "scar-silk") == 9


def test_a_sale_before_the_count_was_set_never_comes_off_it(env):
    """The shelf count already reflects every sale before it."""
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    _set(cx, "scar-silk", 10)
    O.set_order_status(cx, oid, "shipped")
    assert SC.reconcile(cx) == []
    SC.sync_order(cx, oid)
    assert _count(cx, "scar-silk") == 10
    O.set_order_status(cx, oid, "cancelled")        # it took nothing, so returns nothing
    assert _count(cx, "scar-silk") == 10


def test_uncounted_and_made_to_order_and_retired_lines(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "old-silk", "qty": 1}, {"slug": "wholomega", "qty": 2},
                      {"slug": "made-one", "qty": 1}, {"slug": "membership:x", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 9             # the retired slug follows superseded_by
    assert _count(cx, "wholomega") is None          # no count row: nothing created
    with pytest.raises(ValueError):
        _set(cx, "made-one", 3)
    with pytest.raises(ValueError):
        _set(cx, "skin-bundle", 3)


def test_a_count_set_after_an_order_is_not_hit_by_its_resync(env):
    """An order accounted while a product had no count never takes from it later."""
    cx = env["cx"]
    oid = _order(cx, [{"slug": "skin-bundle", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    _set(cx, "moisturize", 10)
    O.set_order_status(cx, oid, "packed")
    O.set_order_status(cx, oid, "shipped")
    assert _count(cx, "moisturize") == 10


def test_a_repayment_after_a_count_is_set_takes_nothing(env):
    """Paid while uncounted, then counted, then paid again (paid_at moves on): the
    order was already accounted for, so it takes nothing."""
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    _set(cx, "scar-silk", 10)
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 10


def test_orders_paid_while_the_flag_was_off_are_not_taken_when_it_turns_on(env, monkeypatch):
    """The self-heal looks back 30 days. Sales before Glen's shelf count are in it."""
    cx = env["cx"]
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    _set(cx, "scar-silk", 10)
    assert SC.reconcile(cx) == [oid]
    assert _count(cx, "scar-silk") == 10


def test_flag_off_moves_nothing(env, monkeypatch):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "0")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10
    assert cx.execute("SELECT COUNT(*) FROM stock_order_moves").fetchone()[0] == 0
    assert SC.on_groovekart_delivery(cx, 1, [{"product_name": "Nope"}], key="k") == {}
    assert SC.list_unmatched(cx) == []
    assert SC.reconcile(cx) == []


def test_a_stock_failure_never_blocks_payment(env, monkeypatch):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    monkeypatch.setattr(SC, "units_for_items", lambda *a, **k: 1 / 0)
    assert O.set_order_payment(cx, oid, method="card", amount_cents=7000) is True
    assert cx.execute("SELECT pay_status FROM orders WHERE id=?",
                      (oid,)).fetchone()[0] == "paid"


# ── self-healing ──────────────────────────────────────────────────────────────

def test_reconcile_heals_a_crash_between_payment_and_the_move(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    cx.execute("UPDATE orders SET pay_status='paid', paid_at=? WHERE id=?", (SC._now(), oid))
    cx.commit()                                     # paid, then the process died
    assert _count(cx, "scar-silk") == 10
    assert SC.reconcile(cx) == [oid]
    assert _count(cx, "scar-silk") == 8
    assert SC.reconcile(cx) == []
    cx.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    cx.commit()                                     # cancelled, then the process died
    assert SC.reconcile(cx) == [oid]
    assert _count(cx, "scar-silk") == 10


def test_order_writes_never_run_the_self_heal(env, monkeypatch):
    """Review round 3: the reconcile stays out of the request path."""
    cx = env["cx"]
    calls = []
    monkeypatch.setattr(SC, "reconcile", lambda *a, **k: calls.append(1) or [])
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.set_order_status(cx, oid, "shipped")
    O.set_order_items_and_total(cx, oid, [{"slug": "scar-silk", "qty": 3}], 7000)
    assert _count(cx, "scar-silk") == 7 and calls == []


def test_an_order_with_nothing_to_count_is_marked_and_skipped(env, monkeypatch):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "membership:x", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    synced = []
    real = SC.sync_order
    monkeypatch.setattr(SC, "sync_order", lambda c, o, cat=None: synced.append(o) or real(c, o, cat))
    assert SC.reconcile(cx) == [] and synced == []


def test_cancelling_a_sale_made_before_the_count_puts_nothing_back(env):
    """Review round 3: sold at 10 -> 8, counted at 8, cancelled: 8, never 10."""
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 8
    _set(cx, "scar-silk", 8)                         # Glen's shelf count
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 8
    O.set_order_status(cx, oid, "shipped")           # reinstated: still outside the count
    assert _count(cx, "scar-silk") == 8
    assert SC.reconcile(cx) == []


def test_a_repayment_keeps_the_first_sale_time(env):
    """Review round 3: set_order_payment re-stamps paid_at; the rule reads the log."""
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)    # 8
    _set(cx, "scar-silk", 8)
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)    # paid_at moves on
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 8


def test_an_upserted_edit_on_a_paid_order_takes_the_difference(env):
    """Review round 3: paid for 2, edited to 5 through upsert_order, takes 3 more."""
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _order(cx, [{"slug": "scar-silk", "qty": 5}]) == oid
    assert _count(cx, "scar-silk") == 5


def test_the_qbo_reprice_backfill_takes_the_difference(env, monkeypatch, tmp_path):
    import app
    from dashboard import qbo_billing
    monkeypatch.setattr(app, "LOG_DB", env["path"])
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: app._PRODUCTS.get("products") or {})
    monkeypatch.setenv("CONSOLE_SECRET", "test-console-secret")
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}], ref="INV-1", source="reorder")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 8
    monkeypatch.setattr(qbo_billing, "get_invoice", lambda ref: {"Line": [
        {"DetailType": "SalesItemLineDetail", "Amount": 350.0,
         "SalesItemLineDetail": {"Qty": 5, "ItemRef": {"name": "Scar Silk"}}}]})
    r = app.app.test_client().post(f"/api/console/orders/reprice-from-qbo?order_id={oid}",
                                   headers={"X-Console-Key": "test-console-secret"})
    assert r.status_code == 200 and r.get_json()["results"][0].get("applied")
    assert _count(cx, "scar-silk") == 5


def test_the_cron_reconcile_takes_its_secret_from_a_header_only(env, monkeypatch):
    import app
    monkeypatch.setattr(app, "LOG_DB", env["path"])
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    c = app.app.test_client()
    assert c.post("/api/cron/stock-reconcile?key=cron-test").status_code == 401
    r = c.post("/api/cron/stock-reconcile", headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200 and r.get_json()["ok"] is True


# ── GrooveKart ────────────────────────────────────────────────────────────────

def _gk(cx, prods, ref="GK1"):
    oid = _order(cx, SC.groovekart_items(prods), ref=ref, source="groovekart")
    return oid, SC.on_groovekart_delivery(cx, oid, prods, key=f"id:{ref}", ref=ref)


def test_groovekart_decrements_on_arrival_with_quantity(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    prods = [{"product_name": "Scar Silk", "product_quantity": "3"}]
    oid, _ = _gk(cx, prods)
    assert _count(cx, "scar-silk") == 7
    _gk(cx, prods)                                  # webhook retry
    assert _count(cx, "scar-silk") == 7
    assert SC.log_for(cx, "scar-silk")[0]["who"] == "groovekart"
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10


def test_groovekart_retry_after_a_count_is_set_takes_nothing(env):
    """Review: a redelivered webhook must never subtract an old sale."""
    cx = env["cx"]
    prods = [{"product_name": "Scar Silk", "product_quantity": "2"},
             {"product_name": "Skin Bundle", "product_quantity": "1"}]
    _gk(cx, prods)                                  # nothing counted yet
    _set(cx, "scar-silk", 10)
    _set(cx, "moisturize", 10)
    _gk(cx, prods)                                  # redelivered
    assert _count(cx, "scar-silk") == 10 and _count(cx, "moisturize") == 10


def test_groovekart_stores_quantity_and_slug_on_the_order(env):
    cx = env["cx"]
    oid, _ = _gk(cx, [{"product_name": "Scar Silk", "product_quantity": "2"},
                      {"product_name": "Mystery Tonic"}])
    items = json.loads(cx.execute("SELECT items_json FROM orders WHERE id=?",
                                  (oid,)).fetchone()[0])
    assert items == [{"name": "Scar Silk", "stock_qty": 2, "stock_slug": "scar-silk"},
                     {"name": "Mystery Tonic", "stock_qty": 1}]
    assert SC.monthly_sales(cx, CAT)["scar-silk"] == pytest.approx(
        2 / (SC.AVG_WINDOW_DAYS / 30.4375))


def test_a_fuzzy_name_match_never_decrements(env):
    """Review: "Scar Silk Plus" contains "Scar Silk"; only an exact match counts."""
    cx = env["cx"]
    from dashboard import practitioner_portal as pp
    assert pp.name_to_slug("Scar Silk Plus", CAT) == "scar-silk"      # the fuzzy tier
    _set(cx, "scar-silk", 10)
    _gk(cx, [{"product_name": "Scar Silk Plus", "product_quantity": "1"}])
    assert _count(cx, "scar-silk") == 10
    assert [u["name"] for u in SC.list_unmatched(cx)] == ["Scar Silk Plus"]


def test_groovekart_unmatched_name_is_listed_once_per_delivery(env):
    cx = env["cx"]
    prods = [{"product_name": "Mystery Tonic", "product_quantity": "2"},
             {"product_name": "Scar Silk", "product_quantity": "bad"}, "junk", {}]
    _set(cx, "scar-silk", 4)
    _gk(cx, prods, ref="GK2")
    _gk(cx, prods, ref="GK2")                       # retry: not listed twice
    un = SC.list_unmatched(cx)
    assert [(u["name"], u["times"], u["qty"], u["last_ref"]) for u in un] == [
        ("Mystery Tonic", 1, 2, "GK2")]
    assert _count(cx, "scar-silk") == 3             # a bad quantity counts as 1
    assert SC.on_groovekart_delivery(cx, None, None, key="x") == {}
    assert SC.on_groovekart_delivery(cx, None, "not a list", key="y") == {}


def test_unmatched_names_are_listed_with_no_order_row(env):
    """Review: no order row still lists once per delivery, keyed on the payload."""
    cx = env["cx"]
    prods = [{"product_name": "Mystery Tonic"}]
    payload = {"customer_email": "a@x.com", "products": prods}
    key = SC.delivery_key(payload)
    assert key.startswith("sha256:") and SC.delivery_key({"id": 5}) == "id:5"
    SC.on_groovekart_delivery(cx, None, prods, key=key)
    SC.on_groovekart_delivery(cx, None, prods, key=key)
    assert [u["times"] for u in SC.list_unmatched(cx)] == [1]
    SC.on_groovekart_delivery(cx, None, prods, key=SC.delivery_key({"id": 6}))
    assert [u["times"] for u in SC.list_unmatched(cx)] == [2]


def _webhook_env(monkeypatch, tmp_path):
    import app
    path = str(tmp_path / "gk.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: app.LOG_DB)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: app._PRODUCTS.get("products") or {})
    monkeypatch.setattr(app, "ghl_onboard_contact", lambda **k: {"contact_id": None})
    monkeypatch.setattr(app, "_log_inbound_lead", lambda *a, **k: None)
    monkeypatch.setattr(app, "_attribute_conversion_by_email", lambda *a, **k: None)
    seen = []

    def _ingest(**k):
        seen.append(k["items"])
        cx = sqlite3.connect(path)
        O.init_orders_table(cx)
        O.upsert_order(cx, source=k["source"], external_ref=k["external_ref"],
                       email=k["email"], items=k["items"])
        cx.close()
    monkeypatch.setattr(app, "_ingest_order", _ingest)

    class NoThread:
        def __init__(self, *a, **k): pass
        def start(self): pass
    monkeypatch.setattr(app.threading, "Thread", NoThread)
    return app, path, seen


def test_groovekart_webhook_stays_total_with_an_unmatched_name(monkeypatch, tmp_path):
    app, path, _seen = _webhook_env(monkeypatch, tmp_path)
    cx = sqlite3.connect(path)
    SC.set_count(cx, "scar-silk", 5, who="t", reason="count")
    cx.close()
    payload = {"id": 77, "customer_email": "b@x.com",
               "products": [{"product_name": "Totally Unknown Thing", "product_quantity": "1"},
                            {"product_name": "Scar Silk", "product_quantity": "2"}]}
    r = app.app.test_client().post("/webhook/groovekart", json=payload)
    assert r.status_code == 200
    assert app.app.test_client().post("/webhook/groovekart", json=payload).status_code == 200
    cx = sqlite3.connect(path)
    assert SC.get_count(cx, "scar-silk") == 3
    assert [(u["name"], u["times"]) for u in SC.list_unmatched(cx)] == [
        ("Totally Unknown Thing", 1)]
    cx.close()
    # a broken stock module still leaves the webhook at 200
    monkeypatch.setattr(SC, "on_groovekart_delivery", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(SC, "groovekart_items", lambda *a, **k: 1 / 0)
    payload["id"] = 78
    assert app.app.test_client().post("/webhook/groovekart", json=payload).status_code == 200


def test_groovekart_order_lines_are_unchanged_with_the_flag_off(monkeypatch, tmp_path):
    app, _path, seen = _webhook_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "")
    payload = {"id": 79, "customer_email": "b@x.com",
               "products": [{"product_name": "Scar Silk", "product_quantity": "2"}]}
    assert app.app.test_client().post("/webhook/groovekart", json=payload).status_code == 200
    assert seen == [[{"name": "Scar Silk"}]]


# ── average monthly sales ─────────────────────────────────────────────────────

def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ph(cx, rows):
    PH.init_purchase_history_table(cx)
    for r in rows:
        cx.execute("INSERT INTO purchase_history (email, slug, purchased_at, source, "
                   "source_ref) VALUES (?,?,?,?,?)", r)
    cx.commit()


def _months():
    return SC.AVG_WINDOW_DAYS / 30.4375


def test_monthly_average_combines_sources_and_dedupes_the_overlap(env):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}], ref="A1", email="Pat@X.com")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    cx.execute("UPDATE orders SET paid_at=? WHERE id=?", (_iso(30), oid))
    unpaid = _order(cx, [{"slug": "scar-silk", "qty": 50}], ref="A2")
    gk = _order(cx, [{"name": "Scar Silk"}], ref="G1", source="groovekart")
    cx.execute("UPDATE orders SET created_at=? WHERE id IN (?,?)", (_iso(40), unpaid, gk))
    _ph(cx, [
        ("pat@x.com", "scar-silk", _iso(35)[:10], "fmp", "F1"),    # same sale as A1: once
        ("pat@x.com", "scar-silk", _iso(60)[:10], "fmp", "F2"),    # 30 days apart: counts
        ("zed@x.com", "scar-silk", _iso(31)[:10], "groovekart", "G9"),
        ("old@x.com", "scar-silk", _iso(400)[:10], "fmp", "F3"),   # outside the window
        ("b@x.com", "skin-bundle", _iso(10)[:10], "fmp", "F4"),    # bundle: 2 + 1
        ("c@x.com", "made-one", _iso(10)[:10], "fmp", "F5"),       # made to order: out
    ])
    avg = SC.monthly_sales(cx, CAT)
    # A1 2 + GK order 1 + F2 1 + G9 1 + bundle 2 = 7
    assert avg["scar-silk"] == pytest.approx(7 / _months())
    assert avg["moisturize"] == pytest.approx(1 / _months())
    assert "made-one" not in avg


def test_a_line_with_no_quantity_is_one_bottle(env):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk"}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert SC.monthly_sales(cx, CAT)["scar-silk"] == pytest.approx(1 / _months())


def test_filemaker_and_groovekart_history_of_one_sale_counts_once(env):
    cx = env["cx"]
    _ph(cx, [
        ("q@x.com", "scar-silk", _iso(50)[:10], "fmp", "F1"),
        ("q@x.com", "scar-silk", _iso(45)[:10], "groovekart", "G1"),   # same sale
        ("q@x.com", "scar-silk", _iso(43)[:10], "fmp", "F2"),          # same source: own sale
    ])
    assert SC.monthly_sales(cx, CAT)["scar-silk"] == pytest.approx(2 / _months())


def test_a_stock_move_keeps_the_average_cache(env, monkeypatch):
    cx = env["cx"]
    calls = []
    real = SC.monthly_sales
    monkeypatch.setattr(SC, "monthly_sales", lambda *a, **k: calls.append(1) or real(*a, **k))
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    _set(cx, "scar-silk", 9)
    assert len(calls) == 1


# ── set_count ─────────────────────────────────────────────────────────────────

def test_set_count_logs_the_change_from_the_value_it_overwrote(env):
    cx = env["cx"]
    _set(cx, "scar-silk", 10)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    res = _set(cx, "scar-silk", 5, reason="delivery from the lab")
    assert res["delta"] == -2
    assert [r["delta"] for r in SC.log_for(cx, "scar-silk")] == [-2, -3, 10]


# ── alerts ────────────────────────────────────────────────────────────────────

def test_low_stock_alert_fires_once_per_crossing_to_each_recipient(env):
    cx, sent = env["cx"], env["sent"]
    _set(cx, "scar-silk", 5)
    assert sent == []
    for i in range(3):
        oid = _order(cx, [{"slug": "scar-silk", "qty": 1}], ref=f"L{i}")
        O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 2
    assert _subjects(sent) == ["Low stock: Scar Silk, 3 left"]     # at 3, not again at 2
    assert _subjects(sent, RAE) == ["Low stock: Scar Silk, 3 left"]
    _set(cx, "scar-silk", 20, reason="delivery from the lab")
    _set(cx, "scar-silk", 1)
    assert _subjects(sent) == ["Low stock: Scar Silk, 3 left", "Low stock: Scar Silk, 1 left"]


def test_a_failed_send_is_retried_and_a_sent_one_is_not(env):
    cx, sent, fail = env["cx"], env["sent"], env["fail"]
    fail.add(RAE)
    _set(cx, "scar-silk", 2)
    assert _subjects(sent) == ["Low stock: Scar Silk, 2 left"] and _subjects(sent, RAE) == []
    fail.clear()
    SC.check_alerts(cx, ["scar-silk"])
    assert _subjects(sent, RAE) == ["Low stock: Scar Silk, 2 left"]
    assert _subjects(sent) == ["Low stock: Scar Silk, 2 left"]
    SC.check_alerts(cx, ["scar-silk"])
    assert len(sent) == 2


def test_a_send_in_flight_is_not_sent_twice(env, monkeypatch):
    cx, sent = env["cx"], env["sent"]
    monkeypatch.setattr(SC, "_spawn", lambda fn: None)      # sends never finish
    _set(cx, "scar-silk", 2)
    assert SC.check_alerts(cx, ["scar-silk"]) == []          # both still pending
    assert sent == []


def test_one_month_supply_alert_fires_once_per_crossing(env, monkeypatch):
    cx, sent = env["cx"], env["sent"]
    monkeypatch.setattr(SC, "monthly_sales_cached", lambda cx, cat=None: {"scar-silk": 8.0})
    _set(cx, "scar-silk", 9)
    assert sent == []
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}], ref="M1")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _subjects(sent) == ["Under one month of Scar Silk: 7 left"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}], ref="M2")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _subjects(sent) == ["Under one month of Scar Silk: 7 left"]


def test_alerts_go_to_glen_and_rae_one_to_each(monkeypatch):
    got = []
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda to, s, b: got.append(to))
    assert SC._recipients() == ("suerae1111@gmail.com", "drglenswartwout@gmail.com")
    assert REAL_SEND_ONE(GLEN, "s", "b") is True and got == [GLEN]
    monkeypatch.setattr(inbox, "send_email", lambda to, s, b: 1 / 0)
    assert REAL_SEND_ONE(RAE, "s", "b") is False
    monkeypatch.setattr(inbox, "send_email", lambda to, s, b: {"error": "quota"})
    assert REAL_SEND_ONE(RAE, "s", "b") is False


# ── console ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def console(monkeypatch, tmp_path):
    import app
    path = str(tmp_path / "console.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: app.LOG_DB)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "test-console-secret")
    return app.app.test_client()


def test_console_needs_the_key(console):
    assert console.get("/api/console/stock").status_code == 401
    assert console.post("/api/console/stock/set", json={}).status_code == 401
    assert console.post("/api/console/stock/reconcile").status_code == 401
    assert console.get("/api/console/stock",
                       headers={"X-Console-Key": "wrong"}).status_code == 401


def test_console_lists_lowest_first_and_sets_with_a_reason(console):
    h = {"X-Console-Key": "test-console-secret"}
    for slug, n in (("scar-silk", 9), ("moisturize", 2)):
        r = console.post("/api/console/stock/set", headers=h,
                         json={"slug": slug, "count": n, "reason": "shelf count"})
        assert r.status_code == 200 and r.get_json()["count"] == n
    assert console.post("/api/console/stock/set", headers=h,
                        json={"slug": "scar-silk", "count": 3}).status_code == 400
    assert console.post("/api/console/stock/set", headers=h,
                        json={"slug": "scar-silk", "count": -1,
                              "reason": "x"}).status_code == 400
    d = console.get("/api/console/stock", headers=h).get_json()
    assert [r["slug"] for r in d["rows"]] == ["moisturize", "scar-silk"]
    assert {"count", "avg_monthly", "months_supply", "name"} <= set(d["rows"][0])
    assert d["unmatched"] == [] and d["low_stock_at"] == 3
    assert any(p["slug"] == "scar-silk" for p in d["products"])
    import app
    cx = sqlite3.connect(app.LOG_DB)
    assert SC.log_for(cx, "scar-silk")[0]["who"] == "console"      # the master key
    cx.close()
    r = console.post("/api/console/stock/reconcile", headers=h)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "synced": []}


def test_console_is_off_with_the_flag(console, monkeypatch):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "")
    h = {"X-Console-Key": "test-console-secret"}
    assert console.get("/api/console/stock", headers=h).status_code == 404
    assert console.post("/api/console/stock/reconcile", headers=h).status_code == 404
    assert console.get("/console/stock").status_code == 200


def test_custom_and_drop_shipped_products_are_never_counted(env, monkeypatch):
    """Glen, 2026-11-06: products named "in Terrain Restore" are custom, and the 15-plate
    water ionizer is drop shipped. Neither can carry a count, neither is offered on the
    Stock page, and an old zero row never takes one off sale."""
    cat = dict(CAT)
    cat["root-chakra-in-terrain-restore"] = {"name": "Root Chakra Essence in  Terrain Restore",
                                             "price_cents": 7000}
    cat["water-ionizer-15plate"] = {"name": "15-Plate Water Ionizer (Living Water)",
                                    "price_cents": 250000}
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: cat)
    cx = env["cx"]
    cat["water-ionizer-9plate"] = {"name": "9-Plate Water Ionizer (Living Water)",
                                   "price_cents": 200000}
    cat["aller-free-he"] = {"name": "Aller-Free Homeo-Energetic Drops", "price_cents": 3000}
    for slug, why in (("root-chakra-in-terrain-restore", "custom"),
                      ("aller-free-he", "custom"),
                      ("water-ionizer-15plate", "drop shipped"),
                      ("water-ionizer-9plate", "drop shipped")):
        with pytest.raises(ValueError, match=why):
            _set(cx, slug, 3)
    offered = {p["slug"] for p in SC.countable_products()}
    assert "scar-silk" in offered
    assert not offered & {"root-chakra-in-terrain-restore", "water-ionizer-15plate"}
    # A zero row written before this rule (as on 6 Nov) blocks nothing.
    cx.execute("INSERT INTO stock_counts (slug, count, set_at, updated_at) VALUES (?,?,?,?)",
               ("water-ionizer-15plate", 0, "x", "x"))
    cx.commit()
    SC._invalidate()
    assert "water-ionizer-15plate" in SC.zero_slugs()
    assert not SC.is_counted_out("water-ionizer-15plate", cat["water-ionizer-15plate"])
    # An order for either takes nothing.
    assert SC.units_for_items([{"slug": "water-ionizer-15plate", "qty": 1},
                               {"slug": "root-chakra-in-terrain-restore", "qty": 1},
                               {"slug": "scar-silk", "qty": 1}], cat) == {"scar-silk": 1}


def test_the_five_plate_ionizer_is_counted_but_never_goes_off_sale(env, monkeypatch):
    """Glen, 2026-11-06: 5 paid-for units wait at the manufacturer, "always keep it
    showing available for sale". It is counted and comes off with orders, but 0 blocks
    nothing."""
    cat = dict(CAT)
    cat["water-ionizer-5plate"] = {"name": "5-Plate Water Ionizer (Living Water)",
                                   "price_cents": 150000}
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: cat)
    cx = env["cx"]
    _set(cx, "water-ionizer-5plate", 1)
    assert "water-ionizer-5plate" in {p["slug"] for p in SC.countable_products()}
    oid = _order(cx, [{"slug": "water-ionizer-5plate", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=150000)
    assert _count(cx, "water-ionizer-5plate") == 0
    SC._invalidate()
    assert "water-ionizer-5plate" in SC.zero_slugs()
    assert not SC.is_counted_out("water-ionizer-5plate", cat["water-ionizer-5plate"])
    assert SC.is_counted_out("scar-silk", cat["scar-silk"]) is False
    _set(cx, "scar-silk", 0)
    assert SC.is_counted_out("scar-silk", cat["scar-silk"])   # the rule is per product


def test_infoceuticals_alert_at_zero_or_at_one_when_they_sell_monthly(env, monkeypatch):
    """Glen, 2026-11-06: "For Infoceuticals, set the alert level to zero, unless the average
    monthly use is 1 or more, then set it to 1." Other products still alert at 3."""
    cat = dict(CAT)
    cat["es1-lymph"] = {"name": "ES1 Lymph and Immune Energetic Star Infoceutical",
                        "price_cents": 4000}
    cat["youth"] = {"name": "Youth Infoceutical", "price_cents": 4000}
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: cat)
    avg = {"es1-lymph": 0.5, "youth": 1.0, "scar-silk": 0.0}
    monkeypatch.setattr(SC, "monthly_sales_cached", lambda cx, c=None: avg)
    cx, sent = env["cx"], env["sent"]

    def low_subjects():
        return [s for s in _subjects(sent) if s.startswith("Low stock")]
    _set(cx, "es1-lymph", 1)                 # slow seller: no alert at 1
    assert low_subjects() == []
    _set(cx, "es1-lymph", 0)                 # ... but at 0
    assert any("ES1" in s for s in low_subjects())
    _set(cx, "youth", 2)
    assert not any("Youth" in s for s in low_subjects())
    _set(cx, "youth", 1)                     # sells 1 a month: alert at 1
    assert any("Youth" in s for s in low_subjects())
    _set(cx, "scar-silk", 3)                 # other products unchanged
    assert any("Scar Silk" in s for s in low_subjects())
    assert SC.low_stock_at(cat["youth"], 1.0) == 1
    assert SC.low_stock_at(cat["es1-lymph"], 0.99) == 0
    assert SC.low_stock_at(cat["scar-silk"], 5) == 3
    # One month of supply: a slow Infoceutical never sends it, a monthly seller does.
    assert not any(s.startswith("Under one month") and "ES1" in s for s in _subjects(sent))
    avg["youth"] = 2.0
    _set(cx, "youth", 1)
    assert any(s.startswith("Under one month") and "Youth" in s for s in _subjects(sent))
    assert SC.month_alert_applies(cat["es1-lymph"], 0.5) is False
    assert SC.month_alert_applies(cat["scar-silk"], 0.5) is True


def test_last_set_names_the_item_counted_last_and_ignores_order_deductions(env):
    # Glen, 2026-11-06: show the item inventoried last, so he keeps his place.
    cx = env["cx"]
    assert SC.last_set(cx) is None
    _set(cx, "scar-silk", 10)
    _set(cx, "moisturize", 4)
    assert SC.last_set(cx)["slug"] == "moisturize"
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    last = SC.last_set(cx)
    assert (last["slug"], last["count"]) == ("moisturize", 4)
    assert last["name"] == CAT["moisturize"]["name"]


def test_no_stock_email_goes_unless_alert_emails_are_switched_on(env, monkeypatch):
    # Glen, 2026-11-06: "Stop sending emails about inventory to Rae ... not emails right now."
    monkeypatch.delenv("STOCK_ALERT_EMAILS", raising=False)
    cx = env["cx"]
    _set(cx, "scar-silk", 1)
    assert env["sent"] == []
    assert cx.execute("SELECT COUNT(*) FROM stock_alerts").fetchone()[0] == 0
    assert cx.execute("SELECT COUNT(*) FROM stock_alert_sends").fetchone()[0] == 0


def test_console_rows_name_what_needs_reordering(env, monkeypatch):
    monkeypatch.delenv("STOCK_ALERT_EMAILS", raising=False)
    cx = env["cx"]
    _set(cx, "scar-silk", 0)
    _set(cx, "moisturize", 2)
    _set(cx, "wholomega", 50)
    needs = {r["slug"]: r["needs"] for r in SC.console_rows(cx)}
    assert needs["scar-silk"] == "out" and needs["moisturize"] == "low"
    assert needs["wholomega"] is None


def test_switching_emails_off_leaves_nothing_to_resend_later(env, monkeypatch):
    # Review round 2: a pending send must not retry, and a sent alert must re-arm.
    cx = env["cx"]
    _set(cx, "scar-silk", 1)                       # emails on: an alert goes
    assert env["sent"]
    cx.execute("UPDATE stock_alert_sends SET state='failed'")
    cx.commit()
    monkeypatch.delenv("STOCK_ALERT_EMAILS", raising=False)
    _set(cx, "moisturize", 50)                     # a check of another product while off
    assert cx.execute("SELECT COUNT(*) FROM stock_alert_sends").fetchone()[0] == 0
    assert cx.execute("SELECT COUNT(*) FROM stock_alerts").fetchone()[0] == 0
    _set(cx, "scar-silk", 1)
    assert cx.execute("SELECT COUNT(*) FROM stock_alerts").fetchone()[0] == 0


def test_a_retired_twin_does_not_show_its_count_beside_the_survivor(env):
    # Glen, 2026-11-06: Serenity Blue Green Balance folded into Serenity, and its 8 bottles
    # moved there. The old row must not show a second 8.
    cx = env["cx"]
    cat = {"serenity": {"name": "Serenity"},
           "serenity-blue-green-balance": {"name": "Serenity Blue Green Balance",
                                           "inactive": True, "superseded_by": "serenity"}}
    SC.ensure_tables(cx)
    for slug in cat:
        cx.execute("INSERT INTO stock_counts (slug, count, set_at, updated_at) VALUES (?, 8, ?, ?)",
                   (slug, "2026-11-06T00:00:00", "2026-11-06T00:00:00"))
    cx.commit()
    assert [r["slug"] for r in SC.console_rows(cx, cat)] == ["serenity"]
