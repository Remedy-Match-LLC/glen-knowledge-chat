"""Read-only console view of referral_redemptions.

Without it, a referral order that credits nothing leaves no trace any page can read, so a
check built on the points ledger alone would stay silent in exactly the failure it exists
to catch.
"""
import importlib
import sqlite3

from dashboard import referrals

KEY = "test-console-secret"


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONSOLE_SECRET", KEY)
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    return appmod


def test_referral_redemptions_lists_rows_with_reward_state(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        referrals.record_redemption(cx, "CODE1", "Owner@X.com", "buyer@x.com", "INV1")
        referrals.record_redemption(cx, "PORTAL", "prac@x.com", "pat@x.com", "INV2",
                                    kind="dispensary_portal")
        referrals.mark_rewarded(cx, "buyer@x.com", reward_cents=900)
    c = appmod.app.test_client()
    r = c.get("/api/console/referral-redemptions", headers={"X-Console-Key": KEY})
    assert r.status_code == 200
    rows = {row["referee_email"]: row for row in r.get_json()["rows"]}
    assert set(rows) == {"buyer@x.com", "pat@x.com"}
    assert rows["buyer@x.com"]["owner_email"] == "owner@x.com"
    assert rows["buyer@x.com"]["order_ref"] == "INV1"
    assert rows["buyer@x.com"]["kind"] == "referral"
    assert rows["buyer@x.com"]["reward_cents"] == 900
    assert rows["buyer@x.com"]["rewarded_at"]
    assert rows["pat@x.com"]["kind"] == "dispensary_portal"
    assert not rows["pat@x.com"]["rewarded_at"]


def test_referral_redemptions_empty_database_is_an_empty_list(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    r = appmod.app.test_client().get("/api/console/referral-redemptions",
                                     headers={"X-Console-Key": KEY})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "rows": []}


def test_referral_redemptions_requires_key(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assert appmod.app.test_client().get("/api/console/referral-redemptions").status_code == 401
