"""Studio Black Friday 2026 claim page (marketing design, Glen 2026-11-12).

A Studio app buyer gives name, email, purchase date and plan. One claim per email.
The offer email is held until marketing's copy is approved. Mail goes through the real
ses_mail path with only the AWS client faked, and threads run inline."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import sqlite3
import threading
from datetime import date, datetime, timezone

import pytest

from dashboard import studio_bf as sbf

GOOD = {"name": " Kai  Lee ", "email": "Kai@X.com", "bought_on": "2026-11-20",
        "plan": "1yr", "list_optin": True}
# 18 Nov 2026, 23:30 in Hawaii is already 19 Nov in UTC.
LATE_18TH = datetime(2026, 11, 19, 9, 30, tzinfo=timezone.utc)
AFTER = date(2026, 11, 28)          # a day after every allowed purchase date
NOW_ROUTE = datetime(2026, 11, 28, 20, 0, tzinfo=timezone.utc)   # 28 Nov in Hawaii


def _cx(tmp_path):
    return sqlite3.connect(str(tmp_path / "s.db"))


# --- the module ---------------------------------------------------------------

def test_clean_claim_trims_and_refuses_bad_input():
    got = sbf.clean_claim(GOOD, today=AFTER)
    assert got == {"name": "Kai Lee", "email": "kai@x.com", "bought_on": date(2026, 11, 20),
                   "plan": "1yr", "list_optin": True}
    assert sbf.clean_claim({**GOOD, "list_optin": "no"}, today=AFTER)["list_optin"] is False
    for bad in ({**GOOD, "name": " "}, {**GOOD, "email": "kai@x"},
                {**GOOD, "bought_on": "2026-11-16"}, {**GOOD, "bought_on": "2026-11-28"},
                {**GOOD, "bought_on": "20 Nov"}, {**GOOD, "plan": "6mo"},
                {**GOOD, "name": "a" * 121}, None):
        with pytest.raises(ValueError):
            sbf.clean_claim(bad, today=AFTER)
    for edge in ("2026-11-17", "2026-11-27"):
        assert sbf.clean_claim({**GOOD, "bought_on": edge}, today=AFTER)["bought_on"].isoformat() == edge


def test_a_purchase_date_still_to_come_is_refused():
    assert sbf.clean_claim(GOOD, today=date(2026, 11, 20))["bought_on"] == date(2026, 11, 20)
    with pytest.raises(ValueError):
        sbf.clean_claim(GOOD, today=date(2026, 11, 19))


def test_last_day_is_the_hawaii_claim_day_plus_14():
    assert sbf.hawaii_day(LATE_18TH) == date(2026, 11, 18)
    assert sbf.last_day_to_buy(date(2026, 12, 6)) == date(2026, 12, 20)
    assert sbf.day_label(date(2026, 12, 20)) == "Sunday 20 December"
    assert sbf.day_label(date(2026, 12, 2)) == "Wednesday 2 December"


def test_claims_close_after_6_december_hawaii_time():
    assert sbf.is_open(datetime(2026, 12, 7, 9, 59, tzinfo=timezone.utc))      # 23:59 on 6 Dec
    assert not sbf.is_open(datetime(2026, 12, 7, 10, 0, tzinfo=timezone.utc))  # 00:00 on 7 Dec


def test_a_repeat_claim_updates_but_keeps_its_first_day_and_tick(tmp_path):
    cx = _cx(tmp_path)
    row, new = sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER), now=LATE_18TH)
    assert new and row["claimed_on"] == "2026-11-18" and row["last_day_to_buy"] == "2026-12-02"
    assert row["source"] == "studio_bf_2026" and row["status"] == "pending"
    later = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
    row, new = sbf.save_claim(cx, sbf.clean_claim(
        {**GOOD, "plan": "3mo", "bought_on": "2026-11-24", "list_optin": False},
        today=AFTER), now=later)
    assert not new
    assert (row["plan"], row["bought_on"], row["plan_label"]) == ("3mo", "2026-11-24", "3 months")
    assert row["claimed_on"] == "2026-11-18" and row["last_day_label"] == "Wednesday 2 December"
    assert row["list_optin"] is True    # an untick never removes a tick
    assert len(sbf.list_claims(cx)) == 1


def test_rae_sets_a_status(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    assert sbf.set_status(cx, "KAI@x.com", "confirmed", "rae")["status"] == "confirmed"
    assert sbf.set_status(cx, "nobody@x.com", "confirmed", "rae") is None
    with pytest.raises(ValueError):
        sbf.set_status(cx, "kai@x.com", "shipped", "rae")


def test_offer_is_held_while_the_copy_is_unset(tmp_path):
    cx = _cx(tmp_path)
    row, _ = sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    assert sbf.OFFER_SUBJECT is None and sbf.OFFER_TEXT is None
    assert sbf.offer_message(row) is None


# --- the routes ---------------------------------------------------------------

class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _SES:
    def __init__(self):
        self.sent = []

    def send_email(self, **kw):
        self.sent.append(kw)
        return {"MessageId": f"m{len(self.sent)}"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    from dashboard import ses_mail
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-console-key")
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    monkeypatch.setattr(sbf, "_now", lambda: NOW_ROUTE)
    monkeypatch.setattr(appmod, "_auth_rate_limited", lambda email: None)
    ses = _SES()
    monkeypatch.setattr(ses_mail, "_client", lambda: ses)
    monkeypatch.setenv("SES_SEND_ENABLED", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "test-set")
    appmod.app.config["TESTING"] = True
    return appmod, appmod.app.test_client(), ses


def _copy(monkeypatch):
    """Release the copy, and let ses_mail reach the fake client. pytest sets
    PYTEST_CURRENT_TEST at the start of each test body, so it is removed here."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sbf, "OFFER_SUBJECT", "Your offer, {name}")
    monkeypatch.setattr(sbf, "OFFER_TEXT", "Aloha {name}. Buy by {last_day}.")


def test_page_and_admin_page_are_served(env):
    _, c, _ = env
    page = c.get("/studio-bf")
    assert page.status_code == 200 and b"/api/studio-bf/claim" in page.data
    assert b'min="2026-11-17" max="2026-11-27"' in page.data
    assert c.get("/admin/studio-bf").status_code == 200


def test_claim_records_and_sends_nothing_while_held(env, monkeypatch):
    appmod, c, ses = env
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)   # so a send would reach the fake
    r = c.post("/api/studio-bf/claim", json=GOOD)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}   # nothing about the claim is echoed
    assert ses.sent == []
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sbf.get_claim(cx, "kai@x.com")["offer_sent_at"] is None


def test_bad_claim_is_refused_with_a_reason(env):
    _, c, ses = env
    r = c.post("/api/studio-bf/claim", json={**GOOD, "plan": "6mo"})
    assert r.status_code == 400 and "plan" in r.get_json()["error"]
    assert ses.sent == []


def test_once_released_the_offer_goes_once_as_marketing_mail(env, monkeypatch):
    appmod, c, ses = env
    _copy(monkeypatch)
    assert c.post("/api/studio-bf/claim", json=GOOD).status_code == 200
    assert c.post("/api/studio-bf/claim", json={**GOOD, "plan": "3mo"}).status_code == 200
    assert len(ses.sent) == 1
    raw = ses.sent[0]["Content"]["Raw"]["Data"].decode()
    assert ses.sent[0]["Destination"] == {"ToAddresses": ["kai@x.com"]}
    assert "Buy by Saturday 12 December." in raw
    assert "List-Unsubscribe" in raw          # marketing mail carries the one-click header


def test_an_opted_out_address_gets_no_offer(env, monkeypatch):
    appmod, c, ses = env
    _copy(monkeypatch)
    from dashboard import email_suppression as es
    with sqlite3.connect(appmod.LOG_DB) as cx:
        es.init_table(cx)
        es.add_optout(cx, "kai@x.com", "test")
    assert c.post("/api/studio-bf/claim", json=GOOD).status_code == 200
    assert ses.sent == []


def test_claims_are_refused_after_the_close(env, monkeypatch):
    _, c, ses = env
    monkeypatch.setattr(sbf, "_now", lambda: datetime(2026, 12, 7, 10, 0, tzinfo=timezone.utc))
    r = c.post("/api/studio-bf/claim", json=GOOD)
    assert r.status_code == 410


def test_console_refuses_when_no_secret_is_set(env, monkeypatch):
    appmod, c, _ = env
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    assert c.get("/api/console/studio-bf", headers={"X-Console-Key": "x"}).status_code == 403
    assert c.get("/api/console/studio-bf").status_code == 403


def test_console_list_and_status_need_the_key(env):
    _, c, _ = env
    c.post("/api/studio-bf/claim", json=GOOD)
    assert c.get("/api/console/studio-bf").status_code == 403
    h = {"X-Console-Key": "test-console-key"}
    claims = c.get("/api/console/studio-bf", headers=h).get_json()["claims"]
    assert [(x["email"], x["bought_on"], x["plan_label"]) for x in claims] == [
        ("kai@x.com", "2026-11-20", "1 year")]
    assert c.post("/api/console/studio-bf", json={"email": "kai@x.com", "status": "confirmed"}
                  ).status_code == 403
    r = c.post("/api/console/studio-bf", headers=h,
               json={"email": "kai@x.com", "status": "confirmed", "by": "rae"})
    assert r.get_json()["claim"]["status"] == "confirmed"
    assert c.post("/api/console/studio-bf", headers=h,
                  json={"email": "x@x.com", "status": "confirmed"}).status_code == 404


# --- review rounds 1 and 2 -------------------------------------------------------

def test_a_changed_date_or_plan_resets_raes_check(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    sbf.set_status(cx, "kai@x.com", "confirmed", "rae")
    row, _ = sbf.save_claim(cx, sbf.clean_claim({**GOOD, "name": "Kai L"}, today=AFTER))
    assert row["status"] == "confirmed" and row["status_by"] == "rae"   # same evidence
    row, _ = sbf.save_claim(cx, sbf.clean_claim({**GOOD, "plan": "3mo"}, today=AFTER))
    assert (row["status"], row["status_by"], row["status_at"]) == ("pending", None, None)


def test_send_marks_unknown_is_kept_none_is_freed_stale_is_retaken(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER), now=NOW_ROUTE)
    assert sbf.pending_offer_emails(cx, now=NOW_ROUTE) == ["kai@x.com"]
    assert sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)
    assert not sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)        # once
    assert sbf.pending_offer_emails(cx, now=NOW_ROUTE) == []
    later = NOW_ROUTE + sbf.STALE_MARK + sbf.timedelta(minutes=1)
    assert sbf.pending_offer_emails(cx, now=later) == ["kai@x.com"]        # never ran
    assert sbf.claim_offer_send(cx, "kai@x.com", now=later)                # so it is retaken
    sbf.record_offer_outcome(cx, "kai@x.com", None)                       # surely not sent
    assert sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)
    sbf.record_offer_outcome(cx, "kai@x.com", "unknown")                  # SES may have it
    assert sbf.pending_offer_emails(cx, now=later) == []
    assert not sbf.claim_offer_send(cx, "kai@x.com", now=later)


def test_claims_made_while_held_are_sent_once_released(env, monkeypatch):
    appmod, c, ses = env
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    h = {"X-Console-Key": "test-console-key"}
    c.post("/api/studio-bf/claim", json=GOOD)
    c.post("/api/studio-bf/claim", json={**GOOD, "email": "b@x.com"})
    assert ses.sent == []
    r = c.post("/api/console/studio-bf", headers=h, json={"action": "send_pending"})
    assert r.status_code == 409
    _copy(monkeypatch)
    r = c.post("/api/console/studio-bf", headers=h, json={"action": "send_pending"})
    assert r.get_json() == {"ok": True, "tried": 2, "outcomes": {"ses": 2}}
    assert sorted(m["Destination"]["ToAddresses"][0] for m in ses.sent) == ["b@x.com", "kai@x.com"]
    r = c.post("/api/console/studio-bf", headers=h, json={"action": "send_pending"})
    assert r.get_json()["tried"] == 0 and len(ses.sent) == 2
    assert c.post("/api/console/studio-bf", json={"action": "send_pending"}).status_code == 403


def test_an_owner_token_is_refused_when_no_secret_is_set(env, monkeypatch):
    appmod, c, _ = env
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    monkeypatch.setattr(appmod, "_owner_token_ok", lambda key: True)
    assert c.get("/api/console/studio-bf", headers={"X-Console-Key": "own"}).status_code == 403


def test_the_rate_limit_refuses_before_anything_is_saved(env, monkeypatch):
    appmod, c, ses = env
    monkeypatch.setattr(appmod, "_auth_rate_limited",
                        lambda email: (appmod.jsonify({"error": "slow down"}), 429))
    assert c.post("/api/studio-bf/claim", json=GOOD).status_code == 429
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sbf.list_claims(cx) == []
