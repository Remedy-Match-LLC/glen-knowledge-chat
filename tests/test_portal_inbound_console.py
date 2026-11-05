"""Console list of client portal messages for the morning check.

Glen, 5 Nov 2026: "add the check on portal chats to the start of day new client message
check process." The vault job reads this route each morning. The text goes to the console
caller only, never to the log."""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

HDRS = {"X-Console-Key": "testkey"}
MARK = "ZQX-INBOUND-5521"


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return importlib.import_module("app")


def _row(cx, email, role, content, at, author=None):
    cx.execute("INSERT INTO portal_chat_messages(email, role, author, content, created_at) "
               "VALUES (?,?,?,?,?)", (email, role, author, content, at))


@pytest.fixture()
def client(tmp_db, monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", tmp_db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    from dashboard import portal_chat as pc
    with sqlite3.connect(tmp_db) as cx:
        pc.init_table(cx)
        cx.execute("CREATE TABLE IF NOT EXISTS people (email TEXT, name TEXT)")
        cx.execute("INSERT INTO people VALUES ('nan@x.com', 'Nan Sherman')")
        _row(cx, "old@x.com", "client", "before the window", "2026-11-03T09:59:59Z", "Old")
        _row(cx, "nan@x.com", "client", "salt in the shower? " + MARK, "2026-11-04T22:35:14Z", "You")
        _row(cx, "nan@x.com", "assistant", "AI answer", "2026-11-04T22:35:15Z", "Ask Dr. Glen")
        _row(cx, "mona@x.com", "client", "which phase am I in?", "2026-11-04T07:44:45Z",
             "Mona Gonsai")
        _row(cx, "mona@x.com", "practitioner", "Glen's answer", "2026-11-04T09:00:00Z", "Dr. Glen")
        _row(cx, "mona@x.com", "client", "Imported from email for reference. (You can now...)",
             "2026-11-05T02:00:29Z", "Mona Gonsai")
        _row(cx, "anon@x.com", "client", "hello", "2026-11-05T01:00:00Z", "You")
        cx.commit()
    return app.app.test_client()


def _get(client, since="2026-11-03T10:00:00Z", headers=HDRS):
    return client.get("/api/console/portal/inbound?since=" + since, headers=headers)


def test_lists_client_rows_in_the_window_with_reply_flags(client):
    r = _get(client)
    assert r.status_code == 200
    j = r.get_json()
    assert j["truncated"] is False
    got = {(m["email"], m["created_at"]): m for m in j["messages"]}
    assert ("old@x.com", "2026-11-03T09:59:59Z") not in got
    assert [m["id"] for m in j["messages"]] == sorted(m["id"] for m in j["messages"])
    nan = got[("nan@x.com", "2026-11-04T22:35:14Z")]
    assert nan["name"] == "Nan Sherman" and MARK in nan["content"]
    assert (nan["human_reply_after"], nan["ai_reply_after"], nan["imported"]) == (False, True, False)
    mona = got[("mona@x.com", "2026-11-04T07:44:45Z")]
    assert mona["name"] == "Mona Gonsai" and mona["human_reply_after"] is True
    imp = got[("mona@x.com", "2026-11-05T02:00:29Z")]
    assert imp["imported"] is True and imp["human_reply_after"] is False
    assert got[("anon@x.com", "2026-11-05T01:00:00Z")]["name"] == ""   # "You" is not a name
    assert all(m["email"] for m in j["messages"])
    assert len(j["messages"]) == 4


def test_only_client_rows_are_listed(client):
    roles = {m["content"] for m in _get(client).get_json()["messages"]}
    assert "AI answer" not in roles and "Glen's answer" not in roles


@pytest.mark.parametrize("headers", [{}, {"X-Console-Key": "wrong"}])
def test_needs_the_console_key(client, headers):
    r = _get(client, headers=headers)
    assert r.status_code == 401 and MARK not in r.get_data(as_text=True)


@pytest.mark.parametrize("since", ["", "yesterday", "2026-11-03", "2026-11-03T10:00:00Z' OR 1=1"])
def test_since_must_be_an_iso_time(client, since):
    assert _get(client, since=since).status_code == 400


def test_the_text_is_never_logged(client, capsys):
    _get(client)
    out = capsys.readouterr()
    assert MARK not in out.out and MARK not in out.err


def test_a_long_window_is_capped_and_says_so(tmp_db, monkeypatch):
    from dashboard import portal_chat as pc
    with sqlite3.connect(tmp_db) as cx:
        pc.init_table(cx)
        for i in range(pc.INBOUND_MAX + 3):
            _row(cx, "a@x.com", "client", f"m{i}", "2026-11-04T00:00:00Z")
        cx.commit()
        rows, truncated = pc.inbound_since(cx, "2026-11-03T00:00:00Z")
    assert len(rows) == pc.INBOUND_MAX and truncated is True
    assert rows[0]["content"] == "m0"
