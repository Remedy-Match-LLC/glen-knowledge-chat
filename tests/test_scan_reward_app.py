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
    # R13: an import failure FAILS this file. A skip would hide every hook test.
    mod = importlib.import_module("app")
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "LOG_DB", db)
    monkeypatch.setattr(mod, "CONSOLE_SECRET", KEY)
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
    mod._init_referral_tables()
    from dashboard import client_scans, scan_freshness
    with sqlite3.connect(db) as cx:
        scan_freshness.init_table(cx)
        client_scans.init_client_scans_table(cx)
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO affiliate_signups (created_at, name, email, slug, token, "
                   "status) VALUES ('2026-11-01', 'Alice', 'alice@x.com', 'alice', 't1', "
                   "'approved')")
        cx.commit()
    mod._test_db = db
    return mod


def _scan_rows(db):
    with sqlite3.connect(db) as cx:
        has = cx.execute("SELECT 1 FROM sqlite_master WHERE name='scan_invites'").fetchone()
        return cx.execute("SELECT friend_email, slug FROM scan_invites").fetchall() if has else []


def _referral_rows(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT email FROM referral_events").fetchall()


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
    assert _referral_rows(app_mod._test_db) == []          # R2: System A sees nothing


def test_invite_uses_its_own_connection_and_cannot_hurt_the_funnel(app_mod, monkeypatch):
    # R14: the funnel's connection is never handed to the invite write, and an
    # invite failure leaves the funnel's journey row in place.
    import begin_funnel
    from dashboard import scan_reward as sr
    monkeypatch.setenv(FLAG, "1")
    seen = {}
    real_unlock = begin_funnel.record_unlock

    def spy_unlock(cx, **kw):
        seen["funnel"] = cx
        return real_unlock(cx, **kw)

    def boom(cx, *a, **k):
        seen["invite"] = cx
        raise RuntimeError("invite write failed")
    monkeypatch.setattr(begin_funnel, "record_unlock", spy_unlock)
    monkeypatch.setattr(sr, "record_invite", boom)
    _unlock(app_mod)
    assert seen["invite"] is not seen["funnel"]
    with sqlite3.connect(app_mod._test_db) as cx:
        assert cx.execute("SELECT email FROM journey_state WHERE session_id='s1'"
                          ).fetchone()[0] == "friend@y.com"


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
        sr._apply(cx, {"action": "credit", "email": "alice@x.com", "delta": 1,
                       "reason": "t", "event_key": "k", "friend": "", "slug": "",
                       "detail": ""})
        cx.commit()
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


def test_portal_page_is_byte_identical_when_off(app_mod, monkeypatch):
    # R12: the static file is the pre-change file, and flag off serves it through the
    # pre-change send_from_directory path. Flag on differs by the card script only.
    import subprocess
    from dashboard import scan_reward as sr
    repo = Path(__file__).resolve().parent.parent
    head = subprocess.run(["git", "show", "HEAD:static/client-portal.html"], cwd=repo,
                          capture_output=True, check=True).stdout
    assert (repo / "static" / "client-portal.html").read_bytes() == head
    monkeypatch.delenv(FLAG, raising=False)
    off = app_mod.app.test_client().get("/portal/sometoken")
    assert off.status_code == 200 and b"account-reading-credits" not in off.data
    assert off.headers.get("ETag")          # still the static-file response
    monkeypatch.setenv(FLAG, "1")
    on = app_mod.app.test_client().get("/portal/sometoken").data
    assert b"account-reading-credits" in on
    assert on.replace(sr.PORTAL_CARD_JS.encode(), b"", 1) == off.data


def test_fill_endpoint_applies_only_on_json_true(app_mod, monkeypatch):
    # R11: "true", 1 and "yes" count only.
    monkeypatch.setenv(FLAG, "1")
    with sqlite3.connect(app_mod._test_db) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY "
                   "AUTOINCREMENT, email TEXT UNIQUE)")
        cx.execute("INSERT OR IGNORE INTO people (email) VALUES ('a@y.com')")
        cx.commit()
    c = app_mod.app.test_client()
    rows = [{"email": "a@y.com", "client_id": 7}]
    for junk in ("true", 1, "yes"):
        out = c.post("/api/console/people/e4l-client-ids", headers={"X-Console-Key": KEY},
                     json={"rows": rows, "apply": junk}).get_json()
        assert out["applied"] is False and out["set"] == 0
    out = c.post("/api/console/people/e4l-client-ids", headers={"X-Console-Key": KEY},
                 json={"rows": rows, "apply": True}).get_json()
    assert out["set"] == 1
