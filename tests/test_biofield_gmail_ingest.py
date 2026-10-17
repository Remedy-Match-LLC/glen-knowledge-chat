"""A client's own emails feed Mine comms (Glen, 2026-10-16: "no review").

A fake Gmail service stands in for the mailbox, so no test reads real mail. Every person
here is invented.
"""
import base64
import io
import json
import sqlite3
import time

import pytest

from dashboard import biofield_gmail as bg


_NOW_MS = int(time.time() * 1000) - 86400_000   # yesterday


_FROM = {"name": "From", "value": "Pat Testerson <Pat.Test@example.com>"}


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


def _msg(mid, subject, body, ms=None, parts=()):
    return {"id": mid, "internalDate": str(ms or _NOW_MS), "payload": {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "Subject", "value": subject}, _FROM],
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
    pytest.importorskip("pypdf")
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
    m = {"id": "h1", "internalDate": str(_NOW_MS), "payload": {
        "mimeType": "text/html", "headers": [_FROM], "body": {"data": _b(
            "<p>Low&nbsp;energy</p><script>x()</script>")}}}
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: FakeGmail([m]))
    assert "Low energy" in out["text"] and "x()" not in out["text"]


def test_an_image_is_named_but_not_read():
    assert bg.attachment_text("image/jpeg", "eye.jpg", b"\xff\xd8") == ""
    assert bg.attachment_text("text/rtf", "notes.rtf", b"{\\rtf1\\ansi Hi}") == ""



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
    # Text after the attribution line stays: a client may answer inline.
    assert bg.strip_quoted("On Tue, 14 Oct 2026, Dr Glen wrote:\n> Sleep?\nBadly") == "Badly"
    outlook = "My reply\nFrom: Dr Glen <g@example.com>\nSent: Monday\nHis old advice"
    assert bg.strip_quoted(outlook) == "My reply"
    assert bg.strip_quoted("From: work I came home tired") == "From: work I came home tired"
    far = "From: June I felt off\na\nb\nc\nDate: 3 May it eased"
    assert bg.strip_quoted(far) == far


def _cx(tmp_path):
    return sqlite3.connect(str(tmp_path / "chat.db"))


def test_an_unreadable_e4l_db_holds_the_mail(tmp_path):
    svc = FakeGmail([_msg("m1", "hi", "x")])
    out = bg.client_email_text(_cx(tmp_path), "pat.test@example.com",
                               str(tmp_path / "missing.db"), lambda: svc)
    assert out["text"] == "" and "E4L could not be checked" in out["error"]
    assert svc.calls == []


def test_non_latin_names_are_still_two_people(tmp_path):
    p = tmp_path / "e.db"; cx = sqlite3.connect(str(p))
    cx.executescript("""CREATE TABLE e4l_clients(client_id INTEGER, name TEXT, email TEXT);
        INSERT INTO e4l_clients VALUES(1,'田中','x.test@example.com');
        INSERT INTO e4l_clients VALUES(2,'佐藤','x.test@example.com');""")
    cx.commit(); cx.close()
    assert bg.people_on_address(str(p), "x.test@example.com") == 2


def test_mail_older_than_the_window_is_not_mined(tmp_path, e4l):
    import time
    old = int((time.time() - 400 * 86400) * 1000)
    cx = _cx(tmp_path)
    bg.client_email_text(cx, "pat.test@example.com", e4l,
                         lambda: FakeGmail([_msg("o1", "old", "Ancient complaint", ms=old)]))
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: FakeGmail([]))
    assert "Ancient complaint" not in out["text"]


def test_an_unreadable_pdf_is_reported(tmp_path, e4l):
    pytest.importorskip("pypdf")
    assert bg.attachment_text("application/pdf", "bad.pdf", b"not a pdf") is None
    svc = FakeGmail([_msg("m1", "labs", "see attached", parts=[
        {"mimeType": "application/pdf", "filename": "x.pdf", "body": {"data": _b(b"junk")}}])])
    out = bg.client_email_text(_cx(tmp_path), "pat.test@example.com", e4l, lambda: svc)
    assert "1 attachment(s) could not be read." in out["error"]


def test_a_body_handed_by_attachment_id_is_fetched(tmp_path, e4l):
    m = {"id": "b1", "internalDate": str(_NOW_MS), "payload": {
        "mimeType": "text/plain", "headers": [_FROM], "body": {"attachmentId": "big"}}}
    svc = FakeGmail([m], attachments={"big": "A long letter about headaches"})
    out = bg.client_email_text(_cx(tmp_path), "pat.test@example.com", e4l, lambda: svc)
    assert "headaches" in out["text"]


def test_html_quoted_history_is_dropped(tmp_path, e4l):
    html = ('<div>My own words</div><div class="gmail_quote">On Mon wrote<br>'
            'Dr Glen old advice</div>')
    m = {"id": "q1", "internalDate": str(_NOW_MS), "payload": {
        "mimeType": "text/html", "headers": [_FROM], "body": {"data": _b(html)}}}
    out = bg.client_email_text(_cx(tmp_path), "pat.test@example.com", e4l,
                               lambda: FakeGmail([m]))
    assert "My own words" in out["text"] and "old advice" not in out["text"]
    assert "x" not in bg._html_to_text("<blockquote>x</blockquote>")


def test_a_token_for_another_mailbox_is_refused(monkeypatch):
    import types, sys as _s
    class Svc(FakeGmail):
        def getProfile(self, userId):
            return _Exec({"emailAddress": "someone.else@example.com"})
    fake_creds = types.SimpleNamespace(valid=True)
    mods = {"google.oauth2.credentials": types.SimpleNamespace(
                Credentials=types.SimpleNamespace(from_authorized_user_file=lambda p: fake_creds)),
            "google.auth.transport.requests": types.SimpleNamespace(Request=object),
            "googleapiclient.discovery": types.SimpleNamespace(build=lambda *a, **k: Svc([]))}
    for k, v in mods.items():
        monkeypatch.setitem(_s.modules, k, v)
    with pytest.raises(PermissionError):
        bg.default_service()


def test_the_real_reader_is_never_wired_under_pytest(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(bg, "TOKEN", str(tmp_path / "token.json"))
    (tmp_path / "token.json").write_text("{}")
    called = []
    monkeypatch.setattr(bg, "client_email_text", lambda *a, **k: called.append(1) or {})
    c = create_app(str(tmp_path / "c.db"), scan_lookup=lambda e: _NONE,
                   fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                   interpret_complete=lambda s, u: json.dumps({"stresses": []})).test_client()
    tid = _new(c)
    c.post(f"/author/{tid}/mine-comms", json={})
    assert called == []


def test_a_looser_from_match_is_not_mined_or_fetched_again(tmp_path, e4l):
    m = _msg("x1", "hi", "Someone else's news")
    m["payload"]["headers"] = [{"name": "From", "value": "Al <al.pat.test@example.com>"}]
    svc = FakeGmail([m, _msg("m2", "mine", "My own news")])
    cx = _cx(tmp_path)
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: svc)
    assert "Someone else" not in out["text"] and "My own news" in out["text"]
    assert out["text"].count("email ") == 1
    svc.calls.clear()
    bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: svc)
    assert [c for c in svc.calls if c[0] == "get"] == []


def test_a_photo_or_an_oversized_file_is_never_downloaded(tmp_path, e4l):
    svc = FakeGmail([_msg("p1", "pics", "see", parts=[
        {"mimeType": "image/jpeg", "filename": "eye.jpg", "body": {"attachmentId": "img"}},
        {"mimeType": "application/pdf", "filename": "huge.pdf",
         "body": {"attachmentId": "big", "size": bg.FETCH_MAX + 1}}])])
    bg.client_email_text(_cx(tmp_path), "pat.test@example.com", e4l, lambda: svc)
    assert [c for c in svc.calls if c[0] == "attachment"] == []


def test_one_bad_message_does_not_stop_the_rest(tmp_path, e4l):
    svc = FakeGmail([_msg("bad", "x", "x"), _msg("ok", "fine", "Back pain")])
    real_get = svc.get
    def get(userId, id, format=None, messageId=None):
        if id == "bad":
            raise OSError("404")
        return real_get(userId, id, format, messageId)
    svc.get = get
    out = bg.client_email_text(_cx(tmp_path), "pat.test@example.com", e4l, lambda: svc)
    assert "Back pain" in out["text"] and "1 email(s) could not be read" in out["error"]


def test_a_console_fetch_that_raises_still_mines_email(tmp_path):
    def boom(e):
        raise RuntimeError("")
    c = create_app(str(tmp_path / "c.db"), scan_lookup=lambda e: _NONE,
                   fetch_profile=lambda e: {}, fetch_recent_comms=boom,
                   interpret_complete=lambda s, u: json.dumps(
                       {"stresses": ["From email"] if "Floaters" in u else []}),
                   client_gmail=lambda cx, e: {"text": "Floaters", "held": 0,
                                               "error": None}).test_client()
    tid = _new(c)
    j = c.post(f"/author/{tid}/mine-comms", json={}).get_json()
    assert "Only email was mined." in j["error"]


def test_a_forwarded_message_is_named_never_mined(tmp_path, e4l):
    fwd = {"mimeType": "message/rfc822", "filename": "", "body": {}, "parts": [
        {"mimeType": "text/plain", "body": {"data": _b("Her sister: I have migraines")}},
        {"mimeType": "application/pdf", "filename": "sis.pdf", "body": {"attachmentId": "s"}}]}
    svc = FakeGmail([_msg("f1", "fwd", "My own note", parts=[fwd])], attachments={"s": b"x"})
    cx = _cx(tmp_path)
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: svc)
    assert "My own note" in out["text"] and "migraines" not in out["text"]
    atts = json.loads(cx.execute("SELECT attachments FROM client_gmail_messages").fetchone()[0])
    assert [a["filename"] for a in atts] == ["forwarded message"]
    assert [c for c in svc.calls if c[0] == "attachment"] == []
    inline = "See below\n---------- Forwarded message ---------\nFrom: Sis\nDate: x\nMigraines"
    assert bg.strip_quoted(inline) == "See below"
    assert bg.strip_quoted("Mine\n" + "_" * 32 + "\nTheirs") == "Mine"


def test_one_message_found_for_two_clients_is_kept_for_its_sender(tmp_path, e4l):
    m = _msg("shared", "hi", "Al's own words")
    m["payload"]["headers"] = [{"name": "From", "value": "al.pat.test@example.com"}]
    cx = _cx(tmp_path)
    bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: FakeGmail([m]))
    out = bg.client_email_text(cx, "al.pat.test@example.com", e4l, lambda: FakeGmail([m]))
    assert "Al's own words" in out["text"]


def test_a_failed_message_is_fetched_again_next_time(tmp_path, e4l):
    cx = _cx(tmp_path)
    bad = FakeGmail([_msg("r1", "x", "Second try works")])
    real = bad.get
    bad.get = lambda userId, id, format=None, messageId=None: (_ for _ in ()).throw(OSError())
    bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: bad)
    good = FakeGmail([_msg("r1", "x", "Second try works")])
    assert "Second try works" in bg.client_email_text(cx, "pat.test@example.com", e4l,
                                                      lambda: good)["text"]


def test_a_missing_pdf_reader_leaves_the_message_for_a_retry(tmp_path, e4l, monkeypatch):
    import builtins
    real_import = builtins.__import__
    def no_pypdf(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    svc = FakeGmail([_msg("p1", "labs", "see", parts=[
        {"mimeType": "application/pdf", "filename": "l.pdf", "body": {"attachmentId": "a"}}])],
        attachments={"a": b"%PDF"})
    cx = _cx(tmp_path)
    out = bg.client_email_text(cx, "pat.test@example.com", e4l, lambda: svc)
    assert "could not be read; they are retried" in out["error"]
    assert cx.execute("SELECT count(*) FROM client_gmail_messages").fetchone()[0] == 0


def test_the_fetch_stops_at_its_time_budget(tmp_path, e4l):
    ticks = iter(range(0, 1000, 20))
    svc = FakeGmail([_msg(f"t{i}", "s", f"msg {i}") for i in range(5)])
    got = bg.ingest(_cx(tmp_path), "pat.test@example.com", svc, budget=30,
                    clock=lambda: next(ticks))
    assert got["new"] < 5 and got["failed"] == 5 - got["new"]


def test_an_older_table_shape_is_rebuilt(tmp_path):
    cx = _cx(tmp_path)
    cx.execute("CREATE TABLE client_gmail_messages (message_id TEXT PRIMARY KEY, "
               "client_email TEXT, sent_at TEXT, subject TEXT, body TEXT, attachments TEXT, "
               "fetched_at TEXT)")
    bg.ensure_table(cx)
    cols = {r[1] for r in cx.execute("PRAGMA table_info(client_gmail_messages)")}
    assert "kept" in cols
