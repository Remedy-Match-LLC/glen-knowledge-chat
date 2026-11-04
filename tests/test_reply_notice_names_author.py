"""The reply notice names whoever wrote the reply.

Glen, 4 Nov 2026: "routine replies should be handled by Emary ... we sign based on who
wrote it or edited and approved the final draft." The notice used to say "Dr. Glen
replied to you" whoever wrote the reply, so a reply from Rae or Emary reached the
client as his."""
import importlib
import sys
from pathlib import Path

import pytest

HDRS = {"X-Console-Key": "testkey"}
EMAIL = "kai@example.com"


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    # A failed import fails these tests. Skipping would let CI pass without them
    # (review round 2).
    return importlib.import_module("app")


@pytest.fixture()
def sent(tmp_db, monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", tmp_db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    import sqlite3
    from dashboard import client_portal as cp
    with sqlite3.connect(tmp_db) as cx:
        cp.init_client_portal_table(cx)
    calls = []
    result = [("gmail-api", None)]
    monkeypatch.setattr(app, "_send_full_report_email",
                        lambda email, name, subject, body, *a, **k:
                        calls.append((subject, body)) or result[0])
    c = app.app.test_client()

    def post(**payload):
        r = c.post(f"/api/console/portal/{EMAIL}/message", json={"content": "Aloha.", **payload},
                   headers=HDRS)
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()
    post.result = result
    post.db = tmp_db
    return post, calls


@pytest.mark.parametrize("author", ["Emary", "Rae", "Dr. Glen"])
def test_the_notice_names_the_replys_author(sent, author):
    post, calls = sent
    assert post(author=author)["notified"] is True
    subject, body = calls[-1]
    assert subject == f"{author} replied to you 🌺"
    assert f"{author} just replied to you" in body


def test_emarys_reply_never_says_dr_glen_replied(sent):
    post, calls = sent
    post(author="Emary")
    subject, body = calls[-1]
    assert "Dr. Glen replied" not in subject
    assert "Dr. Glen just replied" not in body


def test_no_author_still_reads_dr_glen(sent):
    post, calls = sent
    post()
    assert calls[-1][0] == "Dr. Glen replied to you 🌺"


def test_an_author_cannot_break_the_subject_line(sent):
    post, calls = sent
    post(author="Rae\r\nBcc: someone@example.com")
    subject = calls[-1][0]
    assert "\n" not in subject and "\r" not in subject


def test_notify_false_sends_nothing(sent):
    post, calls = sent
    assert post(author="Emary", notify=False)["notified"] is False
    assert calls == []


def test_a_blank_author_from_any_caller_falls_back_to_dr_glen():
    app = _app()
    assert app._reply_author_name("  \n ") == "Dr. Glen"
    assert app._reply_author_name(None) == "Dr. Glen"


def test_the_notice_is_signed_by_the_author_not_dr_glen_and_rae(sent):
    post, calls = sent
    post(author="Emary")
    body = calls[-1][1]
    assert body.rstrip().endswith("With aloha,\nEmary")
    assert "Dr. Glen & Rae" not in body


def test_the_chat_row_and_the_email_carry_the_same_name(sent):
    import sqlite3
    post, calls = sent
    post(author="  Rae \n Luscombe ")
    with sqlite3.connect(post.db) as cx:
        stored = cx.execute("SELECT author FROM portal_chat_messages ORDER BY id DESC").fetchone()[0]
    assert stored == "Rae Luscombe"
    assert calls[-1][0] == "Rae Luscombe replied to you 🌺"


def test_a_long_author_is_cut_to_60_characters(sent):
    post, calls = sent
    post(author="E" * 80)
    assert calls[-1][0] == "E" * 60 + " replied to you 🌺"


def test_a_non_text_author_does_not_break_the_route(sent):
    post, calls = sent
    post(author=123)
    assert calls[-1][0] == "123 replied to you 🌺"


@pytest.mark.parametrize("via", [("suppressed", None), ("console-log", "no email-send mechanism")])
def test_notified_is_false_when_no_mail_went_out(sent, via):
    post, calls = sent
    post.result[0] = via
    assert post(author="Emary")["notified"] is False
    assert calls


def test_the_console_sends_the_chosen_author_and_has_no_default():
    html = (Path(__file__).resolve().parent.parent / "static" / "console-biofield-portal.html").read_text()
    assert "{content:content, notify:notify, author:author}" in html
    assert "Send as Dr. Glen" not in html
    sel = html.split('<select id="pcAuthor">')[1].split("</select>")[0]
    assert '<option value="">' in sel and "selected" not in sel
    assert "if(!author)" in html


def test_the_ai_draft_is_unsigned_and_sees_each_staff_author(tmp_db, monkeypatch):
    """Review round 3: the draft prompt signed every draft 'Dr. Glen' and showed every
    earlier staff reply to the model as Dr. Glen's."""
    import sqlite3
    from types import SimpleNamespace
    from dashboard import client_portal as cp
    from dashboard import portal_chat as pc
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", tmp_db)
    monkeypatch.setattr(app, "CONSOLE_SECRET", "testkey")
    with sqlite3.connect(tmp_db) as cx:
        cp.init_client_portal_table(cx)
        pc.add_message(cx, EMAIL, "client", "How do I take it?")
        pc.add_message(cx, EMAIL, "practitioner", "On rising.", author="Emary")
        pc.add_message(cx, EMAIL, "client", "Thanks. And with food?")
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(text="Aloha Kai — on rising.")])
    monkeypatch.setattr(app._cl.messages, "create", create)
    r = app.app.test_client().post(f"/api/console/portal/{EMAIL}/draft-reply", headers=HDRS)
    assert r.status_code == 200
    assert "—" not in r.get_json()["draft"]
    assert "signed 'Dr. Glen'" not in seen["system"]
    assert "Do not add a signature" in seen["system"]
    assert "Sign off as Dr. Glen" not in seen["system"]
    convo = seen["messages"][0]["content"]
    assert "Emary: On rising." in convo
    assert "Dr. Glen: On rising." not in convo
