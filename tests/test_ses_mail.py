"""The SES sender: suppression first, switched off by default, one-click headers on marketing."""
import email
import json
import sqlite3

import pytest

from dashboard import email_suppression as es
from dashboard import ses_mail as sm
from dashboard import unsubscribe as un


class FakeSES:
    def __init__(self):
        self.calls = []

    def send_email(self, **kw):
        self.calls.append(kw)
        return {"MessageId": "ses-1"}


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(un, "_SECRET", "test-secret-abc", raising=False)


def _db():
    cx = sqlite3.connect(":memory:")
    es.init_table(cx)
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, tags TEXT)")
    return cx


def _parsed(call):
    return email.message_from_bytes(call["Content"]["Raw"]["Data"])


def test_off_by_default_sends_nothing(monkeypatch):
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    fake = FakeSES()
    out = sm.send(_db(), "a@example.com", "s", "t", marketing=True, client=fake)
    assert out == {"skipped": "disabled"} and fake.calls == []


def test_under_pytest_without_a_client_never_reaches_aws(monkeypatch):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    monkeypatch.setattr(sm, "_client", lambda: pytest.fail("reached AWS"))
    assert sm.send(_db(), "a@example.com", "s", "t", marketing=False) == {"skipped": "pytest"}


def test_marketing_carries_one_click_headers_and_footer(monkeypatch):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "remedymatch-events")
    fake = FakeSES()
    out = sm.send(_db(), "Reader@Example.com", "News", "Body", "<p>Body</p>",
                  marketing=True, client=fake)
    assert out == {"sent": True, "message_id": "ses-1"}
    call = fake.calls[0]
    assert call["Destination"] == {"ToAddresses": ["reader@example.com"]}
    assert call["ConfigurationSetName"] == "remedymatch-events"
    msg = _parsed(call)
    url = un.unsubscribe_url("reader@example.com")
    assert msg["List-Unsubscribe"] == f"<{url}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    parts = [p.get_payload(decode=True).decode() for p in msg.walk()
             if p.get_content_maintype() == "text"]
    assert len(parts) == 2 and all("Unsubscribe" in p for p in parts)


def test_transactional_has_no_unsubscribe(monkeypatch):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    fake = FakeSES()
    sm.send(_db(), "buyer@example.com", "Your order", "Shipped", marketing=False, client=fake)
    msg = _parsed(fake.calls[0])
    assert msg["List-Unsubscribe"] is None
    assert "Unsubscribe" not in msg.get_payload(decode=True).decode()


@pytest.mark.parametrize("bounce_type", ["hard", "complaint", "optout", "ghl-dnd"])
def test_marketing_stops_at_every_table_block(monkeypatch, bounce_type):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    cx = _db()
    es.add(cx, "x@example.com", bounce_type, "r", "test")
    fake = FakeSES()
    out = sm.send(cx, "x@example.com", "s", "t", marketing=True, client=fake)
    assert out["skipped"] == "suppressed" and fake.calls == []


def test_marketing_stops_at_the_hub_refusal_tag(monkeypatch):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    cx = _db()
    cx.execute("INSERT INTO people (email, tags) VALUES ('p@example.com', ?)",
               (json.dumps([es.UNSUBSCRIBED_TAG]),))
    fake = FakeSES()
    assert sm.send(cx, "p@example.com", "s", "t", marketing=True, client=fake)["skipped"] == "suppressed"
    assert fake.calls == []


@pytest.mark.parametrize("bounce_type,blocked", [
    ("hard", True), ("complaint", True), ("optout", False), ("ghl-dnd", False)])
def test_transactional_stops_only_at_a_dead_address_or_a_complaint(monkeypatch, bounce_type, blocked):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    cx = _db()
    es.add(cx, "x@example.com", bounce_type, "r", "test")
    fake = FakeSES()
    out = sm.send(cx, "x@example.com", "Your order", "t", marketing=False, client=fake)
    assert ("skipped" in out) is blocked
    assert (fake.calls == []) is blocked


def test_blank_address_sends_nothing(monkeypatch):
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    fake = FakeSES()
    assert sm.send(_db(), "  ", "s", "t", marketing=False, client=fake)["skipped"] == "suppressed"
    assert fake.calls == []
