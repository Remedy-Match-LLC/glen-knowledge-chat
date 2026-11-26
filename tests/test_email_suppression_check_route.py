"""POST /api/console/email-suppression/check, for senders outside this app.

Read-only. It runs the addresses through email_suppression.suppression_reasons, which
gives suppression_reason's answer (the function is_suppressed calls) for each address in
batched queries, so an outside sender and an app sender cannot disagree.
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
    monkeypatch.setattr(es, "suppression_reasons", boom)   # the route's batch path
    r = _post(client, ["hub@x.com"])
    assert r.status_code == 500
    assert "blocked" not in r.get_json()


def test_addresses_are_never_logged(client, monkeypatch, capsys, caplog):
    def boom(cx, email):
        raise RuntimeError("failed on " + email)
    caplog.set_level(logging.DEBUG)
    _post(client, list(EXPECTED))
    monkeypatch.setattr(es, "suppression_reason", boom)
    monkeypatch.setattr(es, "suppression_reasons",
                        lambda cx, emails: boom(cx, ",".join(map(str, emails))))
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


# 2026-11-26: the route reads both stores in batches. The batch answer must equal
# suppression_reason for every address, including case variants, an address held by
# two People rows, and more addresses than one batch holds.
def _seeded(tmp_path):
    import app
    cx = sqlite3.connect(str(tmp_path / "eq.db"))
    es.init_table(cx)
    cx.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, tags TEXT)")
    es.add(cx, "hard@x.com", "hard", "NXDOMAIN", "bounce-scan")
    es.add(cx, "blank@x.com", "", "no type", "test")
    rows = [("Hub@X.com", ["consent:unsubscribed"]), ("clean@x.com", ["consent:opted-in"]),
            ("sms@x.com", ["consent:sms-unsubscribed"]), ("hard@x.com", ["consent:opted-in"]),
            ("twice@x.com", ["consent:opted-in"]), ("TWICE@x.com", ["consent:unsubscribed"]),
            ("bad@x.com", None)]
    for em, tags in rows:
        cx.execute("INSERT INTO people (email, tags) VALUES (?,?)",
                   (em, json.dumps(tags) if tags is not None else "not json"))
    cx.commit()
    return cx


ADDRS = [" HARD@x.com ", "blank@x.com", "hub@x.com", "HUB@X.COM", "clean@x.com", "sms@x.com",
         "twice@x.com", "bad@x.com", "nobody@x.com", "", None, 7]


@pytest.mark.parametrize("batch", [500, 1, 2, 3])
def test_batch_equals_one_at_a_time(tmp_path, monkeypatch, batch):
    monkeypatch.setattr(es, "_BATCH_IN", batch)
    cx = _seeded(tmp_path)
    got = es.suppression_reasons(cx, ADDRS)
    want = {es.normalize(a): es.suppression_reason(cx, a) for a in ADDRS if es.normalize(a)}
    # One deliberate difference: two People rows block if either carries the tag.
    assert want.pop("twice@x.com") in (None, "consent:unsubscribed")
    assert got.pop("twice@x.com") == "consent:unsubscribed"
    assert got == want
    assert got["hard@x.com"] == "hard" and got["blank@x.com"] == "suppressed"
    assert got["hub@x.com"] == "consent:unsubscribed"
    assert got["clean@x.com"] is None and got["sms@x.com"] is None


def test_never_looser_than_the_single_check(tmp_path):
    cx = _seeded(tmp_path)
    got = es.suppression_reasons(cx, ADDRS)
    for a in ADDRS:
        if es.normalize(a) and es.suppression_reason(cx, a) is not None:
            assert got[es.normalize(a)] is not None, a


class _Failing:
    """A connection whose query on one table raises, as a Postgres timeout would."""
    def __init__(self, cx, table):
        self._cx, self._table = cx, table
    def execute(self, sql, *a):
        if f"FROM {self._table}" in sql:
            raise sqlite3.OperationalError("canceling statement due to statement timeout")
        return self._cx.execute(sql, *a)


@pytest.mark.parametrize("table", ["email_suppression", "people"])
def test_a_failed_query_raises_never_answers_clean(tmp_path, table):
    cx = _seeded(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        es.suppression_reasons(_Failing(cx, table), ["hub@x.com", "hard@x.com"])


@pytest.mark.parametrize("table", ["email_suppression", "people"])
def test_a_failed_query_is_a_500(client, monkeypatch, table):
    import app
    real = app.db.connect

    class Wrap:
        def __init__(self, c):
            self._c = c
        def __enter__(self):
            return _Failing(self._c.__enter__(), table)
        def __exit__(self, *a):
            return self._c.__exit__(*a)

    monkeypatch.setattr(app.db, "connect", lambda *a, **k: Wrap(real(*a, **k)))
    r = _post(client, ["hub@x.com", "hard@x.com"])
    assert r.status_code == 500
    assert "blocked" not in r.get_json()


def test_the_route_makes_a_bounded_number_of_queries(client, monkeypatch):
    import app
    path = app.LOG_DB
    with sqlite3.connect(path) as cx:   # duplicate People rows must not add queries
        for i in range(50):
            for em, tags in ((f"dup{i}@x.com", ["consent:opted-in"]),
                             (f"DUP{i}@x.com", ["type:client"])):  # email is UNIQUE; case differs
                cx.execute("INSERT INTO people (email, tags, created_at, updated_at) "
                           "VALUES (?,?,?,?)", (em, json.dumps(tags), "", ""))
        cx.commit()
    n = {"q": 0}
    real = app.db.connect

    class Counting:
        def __init__(self, cx):
            self._cx = cx
        def __enter__(self):
            self._in = self._cx.__enter__()
            return self
        def __exit__(self, *a):
            return self._cx.__exit__(*a)
        def execute(self, *a, **k):
            n["q"] += 1
            return self._in.execute(*a, **k)
        def __getattr__(self, name):
            return getattr(self._in, name)

    monkeypatch.setattr(app.db, "connect", lambda *a, **k: Counting(real(*a, **k)))
    emails = [f"p{i}@x.com" for i in range(300)] + [f"dup{i}@x.com" for i in range(50)] \
        + list(EXPECTED)
    r = _post(client, emails)
    assert r.status_code == 200
    assert r.get_json()["blocked"] == EXPECTED
    assert n["q"] <= 4
