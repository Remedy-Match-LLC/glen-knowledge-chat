"""The review-invite cron: the caller `_send_review_invite` never had.

These need the full app import, so only CI can judge them.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import app as appmod
from dashboard import orders as _orders
from dashboard import review_invites as _ri


def _ago(days):
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _db(monkeypatch, tmp_path, *, status="shipped", days_ago=20, email="b@x.com"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(appmod, "LOG_DB", db_path)
    cx = sqlite3.connect(db_path)
    _orders.init_orders_table(cx)
    _ri.init_table(cx)
    cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, email, name, "
        "items_json, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (_ago(days_ago + 2), "test", "o1", email, "Buyer",
         json.dumps([{"slug": "wholomega", "name": "WholOmega", "qty": 1}]),
         status, _ago(days_ago)))
    cx.commit()
    cx.close()
    return db_path


def _post(monkeypatch, qs=""):
    # require_console_key reads CONSOLE_SECRET bound in dashboard/__init__.py at
    # import time. Nulling that module global is what disables the gate; setting
    # the env var alone does nothing once the module is imported.
    import dashboard as _dash
    monkeypatch.setattr(_dash, "CONSOLE_SECRET", "", raising=False)
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    return c.post(f"/api/cron/review-invites{qs}")


def test_cron_is_a_noop_when_reviews_are_dark(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", False)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    r = _post(monkeypatch)
    assert r.status_code == 200
    assert r.get_json()["disabled"] is True
    assert sent == []


def test_cron_sends_and_marks_invited(monkeypatch, tmp_path):
    db_path = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    r = _post(monkeypatch)
    assert r.status_code == 200
    assert r.get_json()["invited"] == 1
    assert sent == [("b@x.com", "wholomega")]

    cx = sqlite3.connect(db_path)
    assert _ri.pending(cx, days=14) == []      # marked, so a second run is silent
    cx.close()


def test_cron_run_twice_sends_once(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    _post(monkeypatch)
    second = _post(monkeypatch)
    assert second.get_json()["invited"] == 0
    assert len(sent) == 1


def test_a_failed_send_is_not_marked_and_retries(monkeypatch, tmp_path):
    """The whole point of returning a bool: a bounce must not consume the invite."""
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    monkeypatch.setattr(appmod, "_send_review_invite", lambda e, n, s: False)
    first = _post(monkeypatch)
    assert first.get_json() == {"invited": 0, "failed": 1, "candidates": 1,
                                "dry_run": False}

    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    second = _post(monkeypatch)
    assert second.get_json()["invited"] == 1
    assert sent == [("b@x.com", "wholomega")]


def test_dry_run_sends_nothing_but_counts(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    r = _post(monkeypatch, "?dry_run=1")
    body = r.get_json()
    assert body["candidates"] == 1
    assert body["invited"] == 0
    assert sent == []


def test_an_unshipped_order_is_not_invited(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path, status="new")
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    assert _post(monkeypatch).get_json()["invited"] == 0
    assert sent == []


def test_the_backlog_is_not_blasted_on_first_enable(monkeypatch, tmp_path):
    """A two-year-old order must not be emailed the day reviews are switched on."""
    _db(monkeypatch, tmp_path, days_ago=700)
    monkeypatch.setattr(appmod, "_REVIEWS_ENABLED", True)
    sent = []
    monkeypatch.setattr(appmod, "_send_review_invite",
                        lambda e, n, s: sent.append((e, s)) or True)
    assert _post(monkeypatch).get_json()["invited"] == 0
    assert sent == []


def test_send_review_invite_returns_true_on_success(monkeypatch, tmp_path):
    """The cron keys idempotency off this bool, so its contract is load-bearing."""
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "t.db"))
    calls = []
    import dashboard.inbox as _inbox
    monkeypatch.setattr(_inbox, "send_email",
                        lambda *a, **k: calls.append(a) or True)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    assert appmod._send_review_invite("b@x.com", "Buyer", slug) is True
    assert len(calls) == 1


def test_send_review_invite_returns_false_when_send_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "t.db"))
    import dashboard.inbox as _inbox

    def _boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(_inbox, "send_email", _boom)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    assert appmod._send_review_invite("b@x.com", "Buyer", slug) is False
