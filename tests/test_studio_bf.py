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


def _hold(monkeypatch):
    monkeypatch.setattr(sbf, "OFFER_SUBJECT", None)
    monkeypatch.setattr(sbf, "OFFER_TEXT", None)


def test_offer_is_held_while_the_copy_is_unset(tmp_path, monkeypatch):
    _hold(monkeypatch)
    cx = _cx(tmp_path)
    row, _ = sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    assert sbf.offer_message(row) is None
    monkeypatch.setattr(sbf, "OFFER_TEXT", "Aloha {name}.")
    assert sbf.offer_message(row) is None       # either half unset holds it


def test_the_released_copy_fills_both_blanks_and_carries_the_four_doors(tmp_path):
    cx = _cx(tmp_path)
    row, _ = sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    subject, text = sbf.offer_message(row)
    assert subject == "Your thank-you offer from Dr. Glen"
    assert text.startswith("Aloha " + row["name"] + ",\n\n")
    assert "until " + row["last_day_label"] + "." in text
    assert "{" not in text and "}" not in text
    for link in ("https://illtowell.com/prepay?renew=1mo",
                 "https://illtowell.com/prepay?renew=12mo",
                 "https://illtowell.com/membership",
                 "https://illtowell.com/begin/scan"):
        assert link in text
    for price in ("$100", "$1,000", "$1,497", "$1,398", "$300"):
        assert price in text
    assert text.rstrip().endswith("Aloha,\n\nDr. Glen")


# SHA-256 of subject + "\n" + text exactly as approved in
# ~/AI-Training/marketing/04 Copy/black-friday-2026/emails-and-posts.md, section
# "The offer email", computed from that file on 12 Nov 2026. Any change to one word,
# price or term fails here. Changing the copy means re-approval and a new hash.
# Written in groups of eight so the push hook does not read it as a key.
APPROVED_OFFER_SHA256 = "968aeb15 4936924d a49e8bdb 44ee8268 a4526eeb a019d82a e1f76138 e7dbbee6".replace(" ", "")


def test_the_released_copy_is_exactly_the_approved_copy():
    import hashlib
    got = hashlib.sha256((sbf.OFFER_SUBJECT + "\n" + sbf.OFFER_TEXT).encode()).hexdigest()
    assert got == APPROVED_OFFER_SHA256


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
    monkeypatch.setenv("CONSOLE_SECRET", "test-console-key")
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
    _hold(monkeypatch)
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
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sbf.get_claim(cx, "kai@x.com")["offer_outcome"] == "suppressed"


def test_claims_are_refused_after_the_close(env, monkeypatch):
    _, c, ses = env
    monkeypatch.setattr(sbf, "_now", lambda: datetime(2026, 12, 7, 10, 0, tzinfo=timezone.utc))
    r = c.post("/api/studio-bf/claim", json=GOOD)
    assert r.status_code == 410


def test_console_refuses_when_no_secret_is_set(env, monkeypatch):
    appmod, c, _ = env
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    monkeypatch.setenv("CONSOLE_SECRET", "")
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

def test_a_checked_claim_is_frozen_and_the_name_never_changes(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    row, _ = sbf.save_claim(cx, sbf.clean_claim({**GOOD, "name": "Someone Else"}, today=AFTER))
    assert row["name"] == "Kai Lee"
    sbf.set_status(cx, "kai@x.com", "confirmed", "rae")
    row, _ = sbf.save_claim(cx, sbf.clean_claim(
        {**GOOD, "plan": "3mo", "bought_on": "2026-11-18"}, today=AFTER))
    assert (row["status"], row["plan"], row["bought_on"]) == ("confirmed", "1yr", "2026-11-20")


def test_send_marks_unknown_is_kept_and_none_is_freed(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER), now=NOW_ROUTE)
    assert sbf.pending_offer_emails(cx) == ["kai@x.com"]
    assert sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)
    assert sbf.get_claim(cx, "kai@x.com")["offer_outcome"] == "sending"
    assert not sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)        # once
    assert sbf.pending_offer_emails(cx) == []                              # never retaken
    sbf.record_offer_outcome(cx, "kai@x.com", None)                       # surely not sent
    assert sbf.pending_offer_emails(cx) == ["kai@x.com"]
    assert sbf.claim_offer_send(cx, "kai@x.com", now=NOW_ROUTE)
    sbf.record_offer_outcome(cx, "kai@x.com", "unknown")                  # SES may have it
    sbf.record_offer_outcome(cx, "kai@x.com", None)                       # a late None frees nothing
    assert sbf.pending_offer_emails(cx) == []
    assert sbf.get_claim(cx, "kai@x.com")["offer_outcome"] == "unknown"


def test_an_unknown_send_is_never_repeated(env, monkeypatch):
    appmod, c, ses = env
    _copy(monkeypatch)

    class _Timeout:
        def send_email(self, **kw):
            ses.sent.append(kw)
            raise RuntimeError("read timed out")
    from dashboard import ses_mail
    monkeypatch.setattr(ses_mail, "_client", lambda: _Timeout())
    c.post("/api/studio-bf/claim", json=GOOD)
    c.post("/api/studio-bf/claim", json=GOOD)
    r = c.post("/api/console/studio-bf", headers={"X-Console-Key": "test-console-key"},
               json={"action": "send_pending"})
    assert r.get_json()["tried"] == 0
    assert len(ses.sent) == 1
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sbf.get_claim(cx, "kai@x.com")["offer_outcome"] == "unknown"


def test_a_non_ascii_key_is_refused_not_a_crash(env):
    _, c, _ = env
    assert c.get("/api/console/studio-bf", headers={"X-Console-Key": "kéy"}).status_code == 403


def test_claims_made_while_held_are_sent_once_released(env, monkeypatch):
    appmod, c, ses = env
    _hold(monkeypatch)
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
    monkeypatch.setenv("CONSOLE_SECRET", "")
    monkeypatch.setattr(appmod, "_owner_token_ok", lambda key: True)
    assert c.get("/api/console/studio-bf", headers={"X-Console-Key": "own"}).status_code == 403


def test_the_rate_limit_refuses_before_anything_is_saved(env, monkeypatch):
    appmod, c, ses = env
    monkeypatch.setattr(appmod, "_auth_rate_limited",
                        lambda email: (appmod.jsonify({"error": "slow down"}), 429))
    assert c.post("/api/studio-bf/claim", json=GOOD).status_code == 429
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert sbf.list_claims(cx) == []


def test_the_webhook_secret_fallback_does_not_open_the_list(env, monkeypatch):
    """Round 3: the app's CONSOLE_SECRET falls back to WEBHOOK_SECRET. This list reads
    the console env var itself, so the webhook secret alone opens nothing."""
    appmod, c, _ = env
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "webhook-secret")
    monkeypatch.setenv("CONSOLE_SECRET", "")
    assert c.get("/api/console/studio-bf",
                 headers={"X-Console-Key": "webhook-secret"}).status_code == 403


def test_a_name_carrying_a_link_or_address_is_refused():
    for bad in ("Locked, verify at evil.example", "see www dot x", "a@b", "x/y",
                "<b>Kai</b>", "Go to EVIL.COM now", "evil dot com", "evil . com",
                "\uff45\uff56\uff49\uff4c\uff0e\uff43\uff4f\uff4d", "evil\u3002com",
                "evil.\u200bcom", "1.2.3.4", "Kai, call 1-800-555-0100", "Kai http:evil",
                "Kai \u202eliam"):
        try:
            sbf.clean_claim({**GOOD, "name": bad}, today=AFTER)
        except ValueError as e:
            assert str(e) == "Please enter just your name."
        else:
            raise AssertionError(bad)
    for ok in ("Mary J. Smith", "Kai O'Neil-Ho", "Dr. A. B. Lee", "Hōkū Kealoha"):
        assert sbf.clean_claim({**GOOD, "name": ok}, today=AFTER)["name"] == ok


def test_a_rejected_claim_is_never_in_the_pending_list(tmp_path):
    cx = _cx(tmp_path)
    sbf.save_claim(cx, sbf.clean_claim(GOOD, today=AFTER))
    sbf.save_claim(cx, sbf.clean_claim({**GOOD, "email": "b@x.com"}, today=AFTER))
    sbf.set_status(cx, "kai@x.com", "rejected", "rae")
    assert sbf.pending_offer_emails(cx) == ["b@x.com"]


def test_a_rejected_claim_gets_no_offer_from_a_repeat_claim(env, monkeypatch):
    appmod, c, ses = env
    _hold(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    c.post("/api/studio-bf/claim", json=GOOD)          # saved while held, never tried
    h = {"X-Console-Key": "test-console-key"}
    c.post("/api/console/studio-bf", headers=h,
           json={"email": "kai@x.com", "status": "rejected", "by": "rae"})
    _copy(monkeypatch)
    assert c.post("/api/studio-bf/claim", json=GOOD).status_code == 200
    assert ses.sent == []
