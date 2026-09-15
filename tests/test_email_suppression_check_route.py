"""POST /api/console/email-suppression/check, for senders outside this app.

Read-only. It runs each address through email_suppression.suppression_reason, the
function is_suppressed calls, so an outside sender and an app sender cannot disagree.
"""
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dashboard import email_suppression as es  # noqa: E402

URL = "/api/console/email-suppression/check"
KEY = {"X-Console-Key": "testkey"}

EXPECTED = {
    "hard@x.com": "hard",
    "dnd@x.com": "ghl-dnd",
    "opt@x.com": "optout",
    "scan@x.com": "bounce-scan",
    "hub@x.com": "consent:unsubscribed",
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")
    import app
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")  # an absent secret opens the gate
    app._init_people_table()
    with sqlite3.connect(path) as cx:
        es.init_table(cx)
        es.add(cx, "hard@x.com", "hard", "NXDOMAIN", "bounce-scan")
        es.add(cx, "dnd@x.com", "ghl-dnd", "GHL email DND", "ghl")
        es.add_optout(cx, "opt@x.com", "unsubscribe-link:global")
        es.add(cx, "scan@x.com", "bounce-scan", "mailbox full", "bounce-scan")
        for email, tags in (("hub@x.com", ["type:client", "consent:unsubscribed"]),
                            ("clean@x.com", ["type:client", "consent:opted-in"]),
                            ("sms@x.com", ["consent:sms-unsubscribed"])):
            cx.execute("INSERT INTO people (email, tags, created_at, updated_at) "
                       "VALUES (?,?,?,?)", (email, json.dumps(tags), "", ""))
        cx.commit()
    return app.app.test_client()


def _post(client, emails, headers=KEY):
    return client.post(URL, json={"emails": emails}, headers=headers)


def test_401_without_the_console_key(client):
    assert _post(client, ["hub@x.com"], headers={}).status_code == 401
    assert _post(client, ["hub@x.com"], headers={"X-Console-Key": "wrong"}).status_code == 401


def test_over_1000_addresses_is_413_and_checks_nothing(client, monkeypatch):
    calls = []
    real = es.suppression_reason
    monkeypatch.setattr(es, "suppression_reason",
                        lambda cx, e: calls.append(e) or real(cx, e))
    r = _post(client, ["hub@x.com"] + [f"a{i}@x.com" for i in range(1000)])
    assert r.status_code == 413
    assert "blocked" not in r.get_json()
    assert calls == []


def test_exactly_1000_addresses_is_allowed(client):
    r = _post(client, [f"a{i}@x.com" for i in range(1000)])
    assert r.status_code == 200 and r.get_json()["checked"] == 1000


def test_checked_counts_distinct_normalised_addresses(client):
    r = _post(client, ["a@x.com", " A@X.com ", "a@x.com", "b@x.com", "", "   "])
    assert r.status_code == 200
    assert r.get_json()["checked"] == 2


def test_mixed_case_and_spaces_match_and_keys_are_as_sent(client):
    sent = [" HUB@X.com ", "Hard@X.COM", "  dnd@x.com"]
    body = _post(client, sent).get_json()
    assert body["blocked"] == {" HUB@X.com ": "consent:unsubscribed",
                               "Hard@X.COM": "hard",
                               "  dnd@x.com": "ghl-dnd"}


def test_each_block_source_is_reported_with_its_reason(client):
    body = _post(client, list(EXPECTED) + ["clean@x.com", "sms@x.com", "nobody@x.com"]).get_json()
    assert body["ok"] is True
    assert body["blocked"] == EXPECTED
    assert body["checked"] == len(EXPECTED) + 3


def test_a_clean_address_is_absent_from_blocked(client):
    body = _post(client, ["clean@x.com", "sms@x.com", "nobody@x.com"]).get_json()
    assert body == {"ok": True, "checked": 3, "blocked": {}}


def test_any_exception_is_500_with_no_blocked_map(client, monkeypatch):
    def boom(cx, email):
        raise RuntimeError("db down for hub@x.com")
    monkeypatch.setattr(es, "suppression_reason", boom)
    r = _post(client, ["hub@x.com"])
    assert r.status_code == 500
    assert "blocked" not in r.get_json()


def test_addresses_are_never_logged(client, monkeypatch, capsys, caplog):
    def boom(cx, email):
        raise RuntimeError("failed on " + email)
    caplog.set_level(logging.DEBUG)
    _post(client, list(EXPECTED))
    monkeypatch.setattr(es, "suppression_reason", boom)
    _post(client, ["hub@x.com"])
    out = capsys.readouterr()
    logged = out.out + out.err + caplog.text
    for email in list(EXPECTED) + ["hub@x.com"]:
        assert email not in logged


def test_is_suppressed_and_the_route_share_one_answer(client):
    import app
    body = _post(client, list(EXPECTED) + ["clean@x.com"]).get_json()
    with sqlite3.connect(app.LOG_DB) as cx:
        for email in list(EXPECTED) + ["clean@x.com"]:
            assert es.is_suppressed(cx, email) is (email in body["blocked"])
