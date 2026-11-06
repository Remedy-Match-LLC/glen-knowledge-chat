"""Scan share reward: the app's hooks, through the real routes, flag on and flag off.

Flag off must be today's behaviour exactly. Each hook is checked both ways.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

FLAG = "SCAN_REWARD_ENABLED"
KEY = "ci-fake-console-secret"


@pytest.fixture
def app_mod(monkeypatch, tmp_path):
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        mod = importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable here: {e}")
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "LOG_DB", db)
    monkeypatch.setattr(mod, "CONSOLE_SECRET", KEY)
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
    mod._init_referral_tables()
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO affiliate_signups (created_at, name, email, slug, token, "
                   "status) VALUES ('2026-11-01', 'Alice', 'alice@x.com', 'alice', 't1', "
                   "'approved')")
        cx.commit()
    mod._test_db = db
    return mod


def _scan_rows(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT email, utm_source FROM referral_events "
                          "WHERE utm_medium='scan-share'").fetchall()


def _unlock(mod):
    c = mod.app.test_client()
    c.set_cookie("amg_session", "s1")
    c.set_cookie("rm_ref", "alice")
    r = c.post("/begin/unlock", json={"trigger": "email", "email": "Friend@Y.com",
                                      "path": "/begin/scan"})
    assert r.status_code == 200
    return r.get_json()


def test_scan_page_email_records_the_inviter_when_on(app_mod, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    _unlock(app_mod)
    assert _scan_rows(app_mod._test_db) == [("friend@y.com", "alice")]


def test_flag_off_records_nothing_and_answers_the_same(app_mod, monkeypatch, tmp_path):
    monkeypatch.delenv(FLAG, raising=False)
    off = _unlock(app_mod)
    assert _scan_rows(app_mod._test_db) == []
    monkeypatch.setenv(FLAG, "1")
    on = _unlock(app_mod)
    assert on == off


def _hub(mod):
    # The seed in _init_referral_tables already holds the scan offer, demoted.
    return mod.app.test_client().get("/affiliate/hub-data/alice").get_json()["offers"]


def test_hub_leads_with_scan_only_when_on(app_mod, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    off = _hub(app_mod)
    assert off[0]["name"] != "Free Bioenergetic Wellness Scan"
    assert all("primary" not in o for o in off)
    monkeypatch.setenv(FLAG, "1")
    on = app_mod.app.test_client().get("/affiliate/hub-data/alice").get_json()["offers"]
    assert on[0]["name"] == "Free Bioenergetic Wellness Scan"
    assert on[0]["url"] == "https://illtowell.com/begin/scan?ref=alice&c=affiliate-e4l"
    assert on[0]["primary"] is True
    assert not on[0]["description"].startswith("Advanced direct link")
    assert sorted(o["name"] for o in on) == sorted(o["name"] for o in off)


def test_console_endpoints_404_when_off_and_fail_closed(app_mod, monkeypatch):
    c = app_mod.app.test_client()
    monkeypatch.delenv(FLAG, raising=False)
    assert c.post("/api/console/reading-credits/spend", json={},
                  headers={"X-Console-Key": KEY}).status_code == 404
    monkeypatch.setenv(FLAG, "1")
    assert c.get("/api/console/reading-credits?email=a@x.com").status_code == 401
    monkeypatch.setattr(app_mod, "CONSOLE_SECRET", "")
    assert c.get("/api/console/reading-credits?email=a@x.com").status_code == 401


def test_spend_through_the_console(app_mod, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    from dashboard import scan_reward as sr
    with sqlite3.connect(app_mod._test_db) as cx:
        sr.init_tables(cx)
        sr._write(cx, email="alice@x.com", delta=1, reason="t", event_key="k")
    c = app_mod.app.test_client()
    h = {"X-Console-Key": KEY}
    r = c.post("/api/console/reading-credits/spend", headers=h,
               json={"email": "alice@x.com", "key": "report-1"})
    assert r.status_code == 200 and r.get_json()["balance"] == 0
    r2 = c.post("/api/console/reading-credits/spend", headers=h,
                json={"email": "alice@x.com", "key": "report-2"})
    assert r2.status_code == 409
    led = c.get("/api/console/reading-credits?email=alice@x.com", headers=h).get_json()
    assert [row["delta"] for row in led["ledger"]] == [-1, 1]


def test_paid_order_hook_is_inert_when_off(app_mod, monkeypatch):
    called = []
    from dashboard import scan_reward as sr
    monkeypatch.setattr(sr, "credit_store_order", lambda *a, **k: called.append(1))
    monkeypatch.delenv(FLAG, raising=False)
    app_mod._scan_reward_on_paid_order({"email": "f@y.com"}, "INV-1")
    assert called == []
    monkeypatch.setenv(FLAG, "1")
    app_mod._scan_reward_on_paid_order({"email": "f@y.com"}, "INV-1")
    assert called == [1]


def test_settle_referral_reaches_the_hook(app_mod, monkeypatch):
    seen = []
    monkeypatch.setattr(app_mod, "_scan_reward_on_paid_order",
                        lambda o, ref: seen.append(ref))
    app_mod._settle_referral({"email": "f@y.com"}, order_ref="INV-9")
    assert seen == ["INV-9"]
