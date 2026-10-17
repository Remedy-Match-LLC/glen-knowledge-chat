"""A client's own emails feed Mine comms (Glen, 2026-10-16: "no review").

A fake Gmail service stands in for the mailbox, so no test reads real mail. Every person
here is invented.
"""
import base64
import io
import json
import sqlite3

import pytest

from dashboard import biofield_gmail as bg


def _b(s):
    return base64.urlsafe_b64encode(s if isinstance(s, bytes) else s.encode()).decode().rstrip("=")


def _pdf(text):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(300, 300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(
        {NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO(); w.write(buf)
    return buf.getvalue()


def _msg(mid, subject, body, ms=1760000000000, parts=()):
    return {"id": mid, "internalDate": str(ms), "payload": {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "Subject", "value": subject}],
        "parts": [{"mimeType": "text/plain", "body": {"data": _b(body)}}, *parts]}}


class FakeGmail:
    """Records every call. Any method that is not a read raises."""
    def __init__(self, messages, attachments=None, fail=False):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.atts = attachments or {}
        self.fail = fail
        self.calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, userId, q, maxResults):
        self.calls.append(("list", q, maxResults))
        if self.fail:
            raise OSError("no network")
        return _Exec({"messages": [{"id": i} for i in self.messages_by_id]})

    def get(self, userId, id, format=None, messageId=None):
        if messageId is not None:
            self.calls.append(("attachment", id))
            return _Exec({"data": _b(self.atts[id])})
        self.calls.append(("get", id, format))
        return _Exec(self.messages_by_id[id])

    def __getattr__(self, name):        # modify, trash, batchModify, send ...
        raise AssertionError(f"Gmail write attempted: {name}")


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


@pytest.fixture
def e4l(tmp_path):
    p = tmp_path / "e4l.db"
    cx = sqlite3.connect(str(p))
    cx.executescript("""CREATE TABLE e4l_clients(client_id INTEGER PRIMARY KEY, name TEXT,
                        email TEXT, species TEXT);
        INSERT INTO e4l_clients VALUES(1,'Pat Testerson','pat.test@example.com','human');
        INSERT INTO e4l_clients VALUES(2,'Lee Sharedhouse','home.test@example.com','human');
        INSERT INTO e4l_clients VALUES(3,'Kim Sharedhouse','home.test@example.com','human');
        INSERT INTO e4l_clients VALUES(4,'Pat  Testerson','pat.test@example.com','human');""")
    cx.commit(); cx.close()
    return str(p)


def test_a_message_and_its_pdf_are_stored_and_read_back(tmp_path, e4l):
    svc = FakeGmail([_msg("m1", "My eyes", "Floaters since June.\n\nOn Mon, Dr Glen wrote:\n> old")],
                    )
    svc.messages_by_id["m1"]["payload"]["parts"].append(
        {"mimeType": "application/pdf", "filename": "labs.pdf",
         "body": {"attachmentId": "a1"}})
    svc.atts["a1"] = _pdf("Ferritin 12 low")
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    out = bg.client_email_text(cx, "Pat.Test@Example.com", e4l, lambda: svc)
    assert out["error"] is None and out["held"] == 0
    assert "Floaters since June." in out["text"] and "> old" not in out["text"]
    assert "attachment labs.pdf" in out["text"] and "Ferritin 12 low" in out["text"]
    assert ("list", "from:pat.test@example.com newer_than:365d", 25) in svc.calls
    # A second run fetches nothing again.
    svc.calls.clear()
    bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: svc)
    assert [c for c in svc.calls if c[0] != "list"] == []


def test_a_shared_household_address_is_held_and_never_read(tmp_path, e4l):
    svc = FakeGmail([_msg("m1", "hi", "secret")])
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    out = bg.client_email_text(cx, "home.test@example.com", e4l, lambda: svc)
    assert out == {"text": "", "held": 2, "error": None}
    assert svc.calls == []


def test_the_same_person_twice_is_not_a_household(e4l):
    assert bg.people_on_address(e4l, "pat.test@example.com") == 1


def test_an_address_with_search_operators_is_refused(tmp_path, e4l):
    svc = FakeGmail([_msg("m1", "hi", "x")])
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    for bad in ("a@b.com OR from:c@d.com", "a@b.com}", "", None, "from:x@y.com"):
        assert bg.client_email_text(cx, bad, e4l, lambda: svc)["text"] == ""
    assert svc.calls == []


def test_gmail_down_still_returns_what_was_stored(tmp_path, e4l):
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    bg.client_email_text(cx, "pat.test@example.com", e4l,
                         lambda: FakeGmail([_msg("m1", "s", "Knee pain")]))
    out = bg.client_email_text(cx, "pat.test@example.com", e4l,
                               lambda: FakeGmail([], fail=True))
    assert "Knee pain" in out["text"] and "Gmail could not be read" in out["error"]


def test_html_only_mail_is_read_as_text(tmp_path, e4l):
    m = {"id": "h1", "internalDate": "1760000000000", "payload": {
        "mimeType": "text/html", "headers": [], "body": {"data": _b(
            "<p>Low&nbsp;energy</p><script>x()</script>")}}}
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: FakeGmail([m]))
    assert "Low energy" in out["text"] and "x()" not in out["text"]


def test_an_image_is_named_but_not_read():
    assert bg.attachment_text("image/jpeg", "eye.jpg", b"\xff\xd8") == ""
    assert bg.attachment_text("application/pdf", "bad.pdf", b"not a pdf") == ""


# --- the route --------------------------------------------------------------------

from biofield_local_app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


def _client(tmp_path, comms, mail, seen):
    def interpret(system, user):
        seen.append(user)
        return json.dumps({"stresses": ["From email"] if "Floaters" in user else []})
    return create_app(str(tmp_path / "c.db"), scan_lookup=lambda e: _NONE,
                      fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: comms,
                      interpret_complete=interpret,
                      client_gmail=lambda cx, e: mail).test_client()


def _new(c):
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Pat Testerson",
                                          "email": "pat.test@example.com", "date": "2026-10-16"})
    return tid


def test_email_text_reaches_mining(tmp_path):
    seen = []
    c = _client(tmp_path, {}, {"text": "Floaters since June.", "held": 0, "error": None}, seen)
    tid = _new(c)
    j = c.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert any("Floaters since June." in u for u in seen)
    labels = {x["label"] for x in c.get(f"/author/{tid}/stresses").get_json()["data"]["active"]}
    assert "From email" in labels and "error" not in j


def test_console_down_still_mines_email_and_says_so(tmp_path):
    c = _client(tmp_path, None, {"text": "Floaters since June.", "held": 0, "error": None}, [])
    tid = _new(c)
    j = c.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert "Only email was mined." in j["error"]
    labels = {x["label"] for x in c.get(f"/author/{tid}/stresses").get_json()["data"]["active"]}
    assert "From email" in labels


def test_console_down_and_no_email_mines_nothing(tmp_path):
    c = _client(tmp_path, None, {"text": "", "held": 0, "error": None}, [])
    tid = _new(c)
    j = c.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert j["added"] == 0 and "Nothing was mined." in j["error"]


def test_a_held_address_is_named_on_the_page(tmp_path):
    c = _client(tmp_path, {}, {"text": "", "held": 2, "error": None}, [])
    tid = _new(c)
    j = c.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert "E4L shows 2 people on this address" in j["error"]


def test_quoted_lines_and_the_reply_history_are_each_dropped():
    assert bg.strip_quoted("Mine\n> theirs\nMine too") == "Mine\n\nMine too".replace("\n\n", "\n")
    assert bg.strip_quoted("Mine\nOn Tue, 14 Oct 2026, Dr Glen wrote:\nTheir unquoted text") == "Mine"
