"""One review-invite email per buyer, listing every product.

Glen, 2026-10-13: "I see 7 emails to Sharon Connour this morning inviting her to
review 7 different products. That should be consolidated into one email with the
7 links. Apply to everyone the review emails go to."
"""
import json
import sqlite3
from datetime import datetime, timedelta

import app as appmod
from dashboard import orders as _orders
from dashboard import review_invites as _ri

PRODUCTS = {"a": "Alpha Drops", "b": "Beta Powder", "c": "Gamma Oil", "d": "Delta Tea"}


def _ago(days):
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _db(monkeypatch, tmp_path, orders):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(appmod, "LOG_DB", db_path)
    cx = sqlite3.connect(db_path)
    _orders.init_orders_table(cx)
    _ri.init_table(cx)
    for i, (email, slugs) in enumerate(orders):
        cx.execute(
            "INSERT INTO orders (created_at, source, external_ref, email, name, "
            "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (_ago(22), "test", f"o{i}", email, "Buyer",
             json.dumps([{"slug": s, "qty": 1} for s in slugs]), "shipped", _ago(20)))
    cx.commit()
    cx.close()
    appmod._init_review_link_tokens()
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: {"slug": s, "name": PRODUCTS[s]} if s in PRODUCTS else None)
    return db_path


def _post(monkeypatch):
    import dashboard as _dash
    monkeypatch.setattr(_dash, "CONSOLE_SECRET", "", raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client().post("/api/cron/review-invites").get_json()


def _capture(monkeypatch, fail_for=()):
    from dashboard import inbox as _inbox
    sent = []

    def fake(to, subject, body, **kw):
        if to in fail_for:
            raise RuntimeError("smtp down")
        sent.append((to, subject, body))
    monkeypatch.setattr(_inbox, "send_email", fake)
    return sent


def _pending(db_path):
    cx = sqlite3.connect(db_path)
    try:
        return _ri.pending(cx, days=14)
    finally:
        cx.close()


def test_a_buyer_with_many_products_gets_one_email_with_every_link(monkeypatch, tmp_path):
    db_path = _db(monkeypatch, tmp_path, [("s@x.com", ["a", "b", "c"]), ("s@x.com", ["d"])])
    sent = _capture(monkeypatch)
    r = _post(monkeypatch)
    assert r["emails"] == 1 and r["invited"] == 4
    (to, subject, body), = sent
    assert to == "s@x.com"
    assert subject == "How are your remedies? Share a quick review"
    for name in PRODUCTS.values():
        assert body.count(f"{name}: ") == 1
    assert body.count("/review/") == 4
    assert _pending(db_path) == []


def test_one_product_keeps_todays_email(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path, [("one@x.com", ["a"])])
    sent = _capture(monkeypatch)
    _post(monkeypatch)
    (to, subject, body), = sent
    assert subject == "How is your Alpha Drops? Share a quick review"


def test_a_failed_send_leaves_the_whole_buyer_for_tomorrow(monkeypatch, tmp_path):
    db_path = _db(monkeypatch, tmp_path, [("bad@x.com", ["a", "b"]), ("ok@x.com", ["c"])])
    sent = _capture(monkeypatch, fail_for=("bad@x.com",))
    r = _post(monkeypatch)
    assert r["failed"] == 2 and r["invited"] == 1 and [s[0] for s in sent] == ["ok@x.com"]
    assert sorted(p["slug"] for p in _pending(db_path)) == ["a", "b"]


def test_the_run_cap_never_splits_a_buyer(monkeypatch, tmp_path):
    db_path = _db(monkeypatch, tmp_path, [("first@x.com", ["a", "b", "c"]), ("next@x.com", ["d"])])
    monkeypatch.setattr(appmod, "REVIEW_INVITE_MAX_PER_RUN", 2)
    sent = _capture(monkeypatch)
    r = _post(monkeypatch)
    assert [s[0] for s in sent] == ["first@x.com"] and r["invited"] == 3
    assert [p["email"] for p in _pending(db_path)] == ["next@x.com"]


def test_a_second_run_sends_nothing(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path, [("s@x.com", ["a", "b"])])
    sent = _capture(monkeypatch)
    _post(monkeypatch)
    _post(monkeypatch)
    assert len(sent) == 1
