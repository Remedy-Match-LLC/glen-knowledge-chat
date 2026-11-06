"""Shelf counts move with orders, once (Glen, 2026-11-06).

Plan: platform/plans/2026-11-06-stock-counts-in-the-app.md. A paid order takes its
bottles off. A cancelled order puts them back. Bundles take from each component.
GrooveKart orders come off on arrival and an unmatched name never breaks the webhook.
Average monthly sales combine every order source and count an overlap once. Glen and Rae
are alerted once per crossing. Nothing moves with STOCK_COUNTS_ENABLED off.
"""
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


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "1")
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: path)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: CAT)
    sent = []
    monkeypatch.setattr(SC, "_notify", lambda s, b: sent.append(s))
    SC._invalidate()
    cx = sqlite3.connect(path)
    O.init_orders_table(cx)
    SC.init_tables(cx)
    yield {"cx": cx, "sent": sent}
    cx.close()
    SC._invalidate()


def _order(cx, items, ref="R1", source="funnel", email="a@x.com"):
    return O.upsert_order(cx, source=source, external_ref=ref, email=email, items=items,
                          total_cents=7000)


def _count(cx, slug):
    return SC.get_count(cx, slug)


def test_a_paid_order_decrements_once_even_if_marked_paid_twice(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="shelf count")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    assert _count(cx, "scar-silk") == 10            # unpaid: nothing leaves
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 7
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.mark_order_paid_keep_status(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 7
    log = SC.log_for(cx, "scar-silk")
    assert [(r["delta"], r["new_count"], r["order_id"]) for r in log] == [
        (-3, 7, oid), (10, 10, None)]
    assert log[0]["who"] == "order" and log[1]["reason"] == "shelf count"


def test_mark_paid_keep_status_decrements_too(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 5, who="t", reason="count")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}])
    O.mark_order_paid_keep_status(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 4


def test_cancel_restores_once(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 4}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 6
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10
    # paid again after the cancel: the order already took its bottles once
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 10


def test_cancelling_an_unpaid_order_puts_nothing_back(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 4}])
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10


def test_a_bundle_decrements_each_component(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    SC.set_count(cx, "moisturize", 10, who="t", reason="count")
    oid = _order(cx, [{"slug": "skin-bundle", "qty": 2}, {"slug": "scar-silk", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=14000)
    assert _count(cx, "scar-silk") == 5             # 2 bundles x 2 + 1 loose
    assert _count(cx, "moisturize") == 8
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10 and _count(cx, "moisturize") == 10


def test_uncounted_and_made_to_order_and_retired_lines(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _order(cx, [{"slug": "old-silk", "qty": 1}, {"slug": "wholomega", "qty": 2},
                      {"slug": "made-one", "qty": 1}, {"slug": "membership:x", "qty": 1}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 9             # the retired slug follows superseded_by
    assert _count(cx, "wholomega") is None          # no count row: nothing created
    with pytest.raises(ValueError):
        SC.set_count(cx, "made-one", 3, who="t", reason="count")
    with pytest.raises(ValueError):
        SC.set_count(cx, "skin-bundle", 3, who="t", reason="count")


def test_flag_off_moves_nothing(env, monkeypatch):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "0")
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10
    assert cx.execute("SELECT COUNT(*) FROM stock_order_moves").fetchone()[0] == 0
    assert SC.on_groovekart_order(cx, 1, [{"product_name": "Nope"}]) == {}
    assert SC.list_unmatched(cx) == []


def test_a_stock_failure_never_blocks_payment(env, monkeypatch):
    cx = env["cx"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 3}])
    monkeypatch.setattr(SC, "units_for_items", lambda *a, **k: 1 / 0)
    assert O.set_order_payment(cx, oid, method="card", amount_cents=7000) is True
    assert cx.execute("SELECT pay_status FROM orders WHERE id=?",
                      (oid,)).fetchone()[0] == "paid"


# ── GrooveKart ────────────────────────────────────────────────────────────────

def test_groovekart_decrements_on_arrival_with_quantity(env):
    cx = env["cx"]
    SC.set_count(cx, "scar-silk", 10, who="t", reason="count")
    oid = _order(cx, [{"name": "Scar Silk"}], ref="GK1", source="groovekart")
    prods = [{"product_name": "Scar Silk", "product_quantity": "3"}]
    SC.on_groovekart_order(cx, oid, prods, ref="GK1")
    assert _count(cx, "scar-silk") == 7
    SC.on_groovekart_order(cx, oid, prods, ref="GK1")     # webhook retry
    assert _count(cx, "scar-silk") == 7
    assert SC.log_for(cx, "scar-silk")[0]["who"] == "groovekart"
    O.set_order_status(cx, oid, "cancelled")
    assert _count(cx, "scar-silk") == 10


def test_groovekart_unmatched_name_is_listed_once_and_never_raises(env):
    cx = env["cx"]
    oid = _order(cx, [{"name": "x"}], ref="GK2", source="groovekart")
    prods = [{"product_name": "Mystery Tonic", "product_quantity": "2"},
             {"product_name": "Scar Silk", "product_quantity": "bad"}, "junk", {}]
    SC.set_count(cx, "scar-silk", 4, who="t", reason="count")
    SC.on_groovekart_order(cx, oid, prods, ref="GK2")
    SC.on_groovekart_order(cx, oid, prods, ref="GK2")     # retry: not listed twice
    un = SC.list_unmatched(cx)
    assert [(u["name"], u["times"], u["qty"], u["last_ref"]) for u in un] == [
        ("Mystery Tonic", 1, 2, "GK2")]
    assert _count(cx, "scar-silk") == 3                   # a bad quantity counts as 1
    assert SC.on_groovekart_order(cx, oid, None) == {}
    assert SC.on_groovekart_order(cx, oid, "not a list") == {}


def test_groovekart_webhook_stays_total_with_an_unmatched_name(monkeypatch, tmp_path):
    import app
    path = str(tmp_path / "gk.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(SC, "_DB_PATH_FN", lambda: app.LOG_DB)
    monkeypatch.setattr(SC, "_CATALOG_FN", lambda: app._PRODUCTS.get("products") or {})
    monkeypatch.setattr(app, "ghl_onboard_contact", lambda **k: {"contact_id": None})
    monkeypatch.setattr(app, "_log_inbound_lead", lambda *a, **k: None)
    monkeypatch.setattr(app, "_attribute_conversion_by_email", lambda *a, **k: None)

    def _ingest(**k):
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
    cx = sqlite3.connect(path)
    SC.set_count(cx, "scar-silk", 5, who="t", reason="count")
    cx.close()
    payload = {"id": 77, "customer_email": "b@x.com",
               "products": [{"product_name": "Totally Unknown Thing", "product_quantity": "1"},
                            {"product_name": "Scar Silk", "product_quantity": "2"}]}
    r = app.app.test_client().post("/webhook/groovekart", json=payload)
    assert r.status_code == 200
    cx = sqlite3.connect(path)
    assert SC.get_count(cx, "scar-silk") == 3
    assert [u["name"] for u in SC.list_unmatched(cx)] == ["Totally Unknown Thing"]
    cx.close()
    # a broken stock module still leaves the webhook at 200
    monkeypatch.setattr(SC, "on_groovekart_order", lambda *a, **k: 1 / 0)
    payload["id"] = 78
    assert app.app.test_client().post("/webhook/groovekart", json=payload).status_code == 200


# ── average monthly sales ─────────────────────────────────────────────────────

def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_monthly_average_combines_sources_and_dedupes_the_overlap(env):
    cx = env["cx"]
    PH.init_purchase_history_table(cx)
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}], ref="A1", email="Pat@X.com")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    cx.execute("UPDATE orders SET paid_at=? WHERE id=?", (_iso(30), oid))
    unpaid = _order(cx, [{"slug": "scar-silk", "qty": 50}], ref="A2")
    gk = _order(cx, [{"name": "Scar Silk"}], ref="G1", source="groovekart")
    cx.execute("UPDATE orders SET created_at=? WHERE id IN (?,?)", (_iso(40), unpaid, gk))
    rows = [
        ("pat@x.com", "scar-silk", _iso(35)[:10], "fmp", "F1"),    # same sale as A1: once
        ("pat@x.com", "scar-silk", _iso(60)[:10], "fmp", "F2"),    # 30 days apart: counts
        ("zed@x.com", "scar-silk", _iso(31)[:10], "groovekart", "G9"),
        ("old@x.com", "scar-silk", _iso(400)[:10], "fmp", "F3"),   # outside the window
        ("b@x.com", "skin-bundle", _iso(10)[:10], "fmp", "F4"),    # bundle: 2 + 1
        ("c@x.com", "made-one", _iso(10)[:10], "fmp", "F5"),       # made to order: out
    ]
    for r in rows:
        cx.execute("INSERT INTO purchase_history (email, slug, purchased_at, source, "
                   "source_ref) VALUES (?,?,?,?,?)", r)
    cx.commit()
    avg = SC.monthly_sales(cx, CAT)
    months = SC.AVG_WINDOW_DAYS / 30.4375
    # A1 2 + GK order 1 + F2 1 + G9 1 + bundle 2 = 7
    assert avg["scar-silk"] == pytest.approx(7 / months)
    assert avg["moisturize"] == pytest.approx(1 / months)
    assert "made-one" not in avg


# ── alerts ────────────────────────────────────────────────────────────────────

def test_low_stock_alert_fires_once_per_crossing(env):
    cx, sent = env["cx"], env["sent"]
    SC.set_count(cx, "scar-silk", 5, who="t", reason="count")
    assert sent == []
    for i in range(3):
        oid = _order(cx, [{"slug": "scar-silk", "qty": 1}], ref=f"L{i}")
        O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert _count(cx, "scar-silk") == 2
    assert sent == ["Low stock: Scar Silk, 3 left"]          # at 3, not again at 2
    SC.set_count(cx, "scar-silk", 20, who="t", reason="delivery from the lab")
    SC.set_count(cx, "scar-silk", 1, who="t", reason="shelf count")
    assert sent == ["Low stock: Scar Silk, 3 left", "Low stock: Scar Silk, 1 left"]


def test_one_month_supply_alert_fires_once_per_crossing(env, monkeypatch):
    cx, sent = env["cx"], env["sent"]
    monkeypatch.setattr(SC, "monthly_sales_cached", lambda cx, cat=None: {"scar-silk": 8.0})
    SC.set_count(cx, "scar-silk", 9, who="t", reason="count")
    assert sent == []
    oid = _order(cx, [{"slug": "scar-silk", "qty": 2}], ref="M1")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert sent == ["Under one month of Scar Silk: 7 left"]
    oid = _order(cx, [{"slug": "scar-silk", "qty": 1}], ref="M2")
    O.set_order_payment(cx, oid, method="card", amount_cents=7000)
    assert sent == ["Under one month of Scar Silk: 7 left"]


def test_alerts_go_to_glen_and_rae_one_to_each(monkeypatch):
    got = []
    from dashboard import inbox
    monkeypatch.setattr(inbox, "send_email", lambda to, s, b: got.append(to))
    SC._send_all("s", "b")
    assert sorted(got) == ["drglenswartwout@gmail.com", "suerae1111@gmail.com"]


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


def test_console_is_off_with_the_flag(console, monkeypatch):
    monkeypatch.setenv("STOCK_COUNTS_ENABLED", "")
    h = {"X-Console-Key": "test-console-secret"}
    assert console.get("/api/console/stock", headers=h).status_code == 404
    assert console.get("/console/stock").status_code == 200
