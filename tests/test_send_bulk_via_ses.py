"""send_bulk tries Amazon SES first, the replacement for GoHighLevel (Glen, 2026-10-29).

It carries the portal welcome, the new-scan notice and the Kloud Mat instructions.
GHL and then Gmail stay as fallbacks only when SES surely sent nothing.
"""
import sqlite3

import pytest

from dashboard import email_suppression as es
from dashboard import inbox, ses_mail, unsubscribe


class FakeSES:
    def __init__(self):
        self.calls, self.fail = [], None

    def send_email(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail()
        return {"MessageId": "mid-1"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    path = str(tmp_path / "inbox.db")
    cx = sqlite3.connect(path)
    es.init_table(cx)
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, email TEXT, tags TEXT)")
    cx.commit()
    cx.close()
    monkeypatch.setattr(inbox, "_db_path", lambda: path)
    monkeypatch.setattr(inbox, "_is_undeliverable", lambda e: False)
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    monkeypatch.setenv("SES_SEND_ENABLED", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "remedymatch-events")
    monkeypatch.setenv("BULK_VIA_GHL", "1")
    fake = FakeSES()
    real = ses_mail.send
    monkeypatch.setattr(ses_mail, "send", lambda *a, **k: real(*a, client=fake, **k))
    other = []
    monkeypatch.setattr(inbox, "send_email", lambda *a, **k: other.append("gmail") or {"via": "gmail"})
    from dashboard import ghl_email
    monkeypatch.setattr(ghl_email, "is_configured", lambda: True)
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda *a, **k: other.append("ghl") or {"id": "g", "via": "ghl"})
    return {"ses": fake, "other": other, "path": path}


def _raw(rig):
    return rig["ses"].calls[0]["Content"]["Raw"]["Data"].decode()


def test_ses_sends_and_nothing_else_does(rig):
    r = inbox.send_bulk("a@b.co", "Your portal", "Hello", from_name="Dr. Glen & Rae")
    assert r == {"id": "mid-1", "via": "ses"} and rig["other"] == []
    raw = _raw(rig)
    assert "From: \"Dr. Glen & Rae\" <drglen@mail.remedymatch.com>" in raw
    assert "Reply-To: drglenswartwout@gmail.com" in raw
    assert "List-Unsubscribe" not in raw and "/email/unsubscribe?" not in raw


def test_a_scoped_send_gets_one_footer_and_the_one_click_headers(rig):
    inbox.send_bulk("a@b.co", "News", "Hello", html="<p>Hello</p>", unsubscribe_scope="global")
    raw = _raw(rig)
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in raw
    assert raw.count("/email/unsubscribe?") == 3      # header, text and html parts


def test_ses_off_keeps_the_ghl_path(rig, monkeypatch):
    monkeypatch.setenv("SES_SEND_ENABLED", "")
    inbox.send_bulk("a@b.co", "s", "b")
    assert rig["ses"].calls == [] and rig["other"] == ["ghl"]


def test_a_refusal_falls_back_to_ghl(rig):
    from botocore.exceptions import ClientError
    rig["ses"].fail = lambda: ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
    inbox.send_bulk("a@b.co", "s", "b")
    assert len(rig["ses"].calls) == 1 and rig["other"] == ["ghl"]


def test_a_timeout_never_sends_a_second_copy(rig):
    from botocore.exceptions import ReadTimeoutError
    rig["ses"].fail = lambda: ReadTimeoutError(endpoint_url="https://x")
    r = inbox.send_bulk("a@b.co", "s", "b")
    assert r.get("unknown") and rig["other"] == []


def test_a_suppressed_address_gets_nothing(rig):
    cx = sqlite3.connect(rig["path"])
    es.add(cx, "a@b.co", "hard", "bounce", "test")
    cx.close()
    assert inbox.send_bulk("a@b.co", "s", "b") == {"skipped": "suppressed"}
    assert rig["ses"].calls == [] and rig["other"] == []


def test_an_optout_still_gets_a_transactional_notice_but_no_marketing(rig):
    cx = sqlite3.connect(rig["path"])
    es.add_optout(cx, "a@b.co", "test")
    cx.close()
    # send_bulk's own check stops both, as it did before SES: the hub opt-out wins.
    assert inbox.send_bulk("a@b.co", "s", "b") == {"skipped": "suppressed"}
    assert inbox.send_bulk("a@b.co", "s", "b", unsubscribe_scope="global") == {
        "skipped": "suppressed"}
    assert rig["ses"].calls == []


def test_the_body_never_reaches_the_logs(rig, capsys):
    from botocore.exceptions import ReadTimeoutError
    rig["ses"].fail = lambda: ReadTimeoutError(endpoint_url="https://x")
    inbox.send_bulk("secret.person@b.co", "s", "SECRET-BODY-1")
    got = capsys.readouterr()
    assert "SECRET-BODY-1" not in got.out + got.err
    assert "secret.person" not in got.out + got.err


def test_deliver_holds_no_connection_across_the_send(rig, monkeypatch):
    open_now, seen = [], []

    class Tracked:
        def __enter__(self):
            open_now.append(1)
            self.c = sqlite3.connect(rig["path"])
            return self.c

        def __exit__(self, *e):
            open_now.pop()
            self.c.close()
    real = ses_mail.send
    monkeypatch.setattr(ses_mail, "send", lambda *a, **k: seen.append(len(open_now)) or real(*a, **k))
    out = ses_mail.deliver(Tracked, "a@b.co", "s", "b", marketing=False)
    assert out == ("ses", "mid-1") and seen == [0]


def test_ses_own_suppression_answer_is_final(rig, monkeypatch):
    """A hard bounce that lands after send_bulk's own check must stop GHL too (round 0)."""
    cx = sqlite3.connect(rig["path"])
    es.add(cx, "a@b.co", "hard", "bounce", "test")
    cx.close()
    monkeypatch.setattr(es, "is_suppressed", lambda *a, **k: False)   # the earlier check missed it
    assert inbox.send_bulk("a@b.co", "s", "b") == {"skipped": "suppressed"}
    assert rig["ses"].calls == [] and rig["other"] == []


def test_a_value_error_after_the_call_is_unknown_not_a_resend(rig):
    rig["ses"].fail = lambda: ValueError("after-call hook")
    r = inbox.send_bulk("a@b.co", "s", "b")
    assert r.get("unknown") and rig["other"] == []


def test_a_failed_suppression_check_logs_no_address(rig, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("row for leak.person@b.co")
    monkeypatch.setattr(es, "is_suppressed", boom)
    inbox.send_bulk("x@b.co", "s", "b")
    assert "leak.person" not in capsys.readouterr().out
