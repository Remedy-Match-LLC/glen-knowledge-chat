"""Findings read from a client's records, reviewed before they reach the Clinical
summary (Glen, 2026-10-16: "review first").

A fake Gmail and a fake model stand in, so no test reads real mail or calls a model.
Every person here is invented.
"""
import base64
import json
import shutil
import sqlite3
import time

import pytest

from dashboard import biofield_gmail as bg
from dashboard import biofield_records as br

EMAIL = "pat.test@example.com"


def _b(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class FakeGmail:
    def __init__(self, files, fail=False):
        self.files = files            # message_id -> {filename: (mime, bytes)}
        self.fail = fail
        self.calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def get(self, userId, id, format=None, messageId=None):
        if messageId is not None:
            self.calls.append(("attachment", messageId, id))
            mid, name = id.split("|", 1)
            return _Exec({"data": _b(self.files[mid][name][1])})
        self.calls.append(("get", id))
        if self.fail:
            raise OSError("no network")
        parts = [{"mimeType": mime, "filename": name, "body": {"attachmentId": f"{id}|{name}",
                                                               "size": len(data)}}
                 for name, (mime, data) in self.files[id].items()]
        return _Exec({"id": id, "payload": {"mimeType": "multipart/mixed", "parts": parts}})

    def __getattr__(self, name):
        raise AssertionError(f"Gmail write attempted: {name}")


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


def _cx(tmp_path, files):
    """A chat db holding the kept mail biofield_gmail would have stored."""
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    bg.ensure_table(cx)
    for mid, atts in files.items():
        cx.execute("INSERT INTO client_gmail_messages(message_id, client_email, sent_at, "
                   "subject, body, attachments, kept, fetched_at) VALUES(?,?,?,?,?,?,1,?)",
                   (mid, EMAIL, "2026-10-01T00:00:00+00:00", "My labs", "",
                    json.dumps([{"filename": n, "mime": m, "text": ""}
                                for n, (m, _d) in atts.items()]), "now"))
    cx.commit()
    return cx


def _model(findings, seen=None):
    def complete(system, user):
        if seen is not None:
            seen.append(user)
        return json.dumps({"findings": findings})
    return complete


def _fake_extract(mime, name, data):
    return [data.decode()]


def test_record_kinds():
    assert br.record_kind("application/pdf", "a.pdf") == "pdf"
    assert br.record_kind("image/jpeg", "scan.jpg") == "image"
    assert br.record_kind("text/rtf", "notes.rtf") == "textutil"
    assert br.record_kind("application/vnd.openxmlformats-officedocument."
                          "wordprocessingml.document", "r.docx") == "textutil"
    assert br.record_kind("text/plain", "r.txt") == "text"
    assert br.record_kind("video/mp4", "clip.mp4") == ""
    assert br.record_kind("message/rfc822", "forwarded message") == ""


def test_a_scanned_pdf_page_is_read_by_ocr_and_a_text_page_is_not():
    pytest.importorskip("pypdf")
    from tests.test_biofield_gmail_ingest import _pdf
    from pypdf import PdfReader, PdfWriter
    import io
    text_pdf = PdfReader(io.BytesIO(_pdf("Ferritin 12 ng/mL low, reference range 30 to 400")))
    w = PdfWriter()
    w.add_page(text_pdf.pages[0])
    w.add_blank_page(300, 300)                       # the "scan"
    buf = io.BytesIO(); w.write(buf)
    asked = []
    pages = br.extract_pages("application/pdf", "labs.pdf", buf.getvalue(),
                             ocr=lambda png: "TSH 4.9 high",
                             rasterise=lambda data, p: asked.append(p) or b"png")
    assert "Ferritin 12" in pages[0] and pages[1] == "TSH 4.9 high" and asked == [2]


def test_an_image_is_read_by_ocr():
    assert br.extract_pages("image/png", "x.png", b"img", ocr=lambda d: " A1c 6.1 ") == ["A1c 6.1"]


@pytest.mark.skipif(not shutil.which("textutil"), reason="macOS textutil only")
def test_an_rtf_record_is_read_as_words_not_markup():
    out = br.extract_pages("text/rtf", "n.rtf", rb"{\rtf1\ansi Ferritin low}")
    assert out == ["Ferritin low"]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs tesseract")
def test_real_ocr_reads_a_rendered_word():
    from PIL import Image, ImageDraw, ImageFont
    import io
    img = Image.new("RGB", (900, 200), "white")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 72)
    except OSError:
        pytest.skip("no Arial")
    ImageDraw.Draw(img).text((20, 50), "Ferritin", fill="black", font=font)
    buf = io.BytesIO(); img.save(buf, "PNG")
    assert "Ferritin" in br.ocr_image(buf.getvalue())


def test_the_model_reply_is_validated():
    raw = [{"kind": "Diagnosis", "text": "Hashimoto thyroiditis", "page": 1, "date": "2026-03-04"},
           {"kind": "advice", "text": "Eat more greens", "page": 1},
           {"kind": "result", "text": "x" * 300, "page": 1},
           {"kind": "result", "text": "TSH 4.9 mIU/L high", "page": 9, "date": "March"},
           "junk"]
    out = br.ask(_model(raw), ["page one", "page two"], "labs.pdf")
    assert out == [{"kind": "diagnosis", "text": "Hashimoto thyroiditis", "page": 1,
                    "date": "2026-03-04"},
                   {"kind": "result", "text": "TSH 4.9 mIU/L high", "page": None, "date": ""}]
    with pytest.raises(ValueError):
        br.ask(lambda s, u: "not json", ["text"], "x.pdf")
    assert br.ask(_model(raw), ["", "  "], "blank.pdf") == []


def test_records_are_read_once_into_pending_findings(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"TSH 4.9 high")}}
    cx = _cx(tmp_path, files)
    svc, seen = FakeGmail(files), []
    got = br.read_records(cx, EMAIL, svc, _model(
        [{"kind": "result", "text": "TSH 4.9 high", "page": 1}], seen), extract=_fake_extract)
    assert got == {"read": 1, "failed": 0, "added": 1, "remaining": 0}
    assert "[page 1]\nTSH 4.9 high" in seen[0]
    f = br.findings(cx, EMAIL)
    assert [x["text"] for x in f["pending"]] == ["TSH 4.9 high"] and f["approved"] == []
    assert f["pending"][0]["filename"] == "labs.pdf" and f["pending"][0]["page"] == 1
    assert f["pending"][0]["subject"] == "My labs"
    svc.calls.clear()
    assert br.read_records(cx, EMAIL, svc, _model([]), extract=_fake_extract)["read"] == 0
    assert svc.calls == []


def test_gmail_failing_leaves_the_record_for_next_time(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    got = br.read_records(cx, EMAIL, FakeGmail(files, fail=True), _model([]),
                          extract=_fake_extract)
    assert got["failed"] == 1 and got["remaining"] == 1


def test_an_unreadable_record_is_named_and_not_read_again(tmp_path):
    files = {"m1": {"broken.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)

    def boom(*a):
        raise ValueError("corrupt")
    got = br.read_records(cx, EMAIL, FakeGmail(files), _model([]), extract=boom)
    assert got["failed"] == 1 and got["remaining"] == 0
    assert br.findings(cx, EMAIL)["failed"] == ["broken.pdf"]


def test_the_read_stops_at_its_time_budget(tmp_path):
    files = {f"m{i}": {f"r{i}.pdf": ("application/pdf", b"x")} for i in range(4)}
    cx = _cx(tmp_path, files)
    ticks = iter(range(0, 1000, 100))
    got = br.read_records(cx, EMAIL, FakeGmail(files), _model([]), budget=150,
                          clock=lambda: next(ticks), extract=_fake_extract)
    assert got["read"] < 4 and got["remaining"] == 4 - got["read"]


def test_approve_with_rewording_reject_and_the_diagnosis_list(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    br.read_records(cx, EMAIL, FakeGmail(files), _model([
        {"kind": "diagnosis", "text": "Hashimotos", "page": 1},
        {"kind": "medication", "text": "Levothyroxine 50 mcg", "page": 1}]),
        extract=_fake_extract)
    ids = {x["text"]: x["id"] for x in br.findings(cx, EMAIL)["pending"]}
    assert br.decide(cx, EMAIL, ids["Hashimotos"], "approve", "Hashimoto thyroiditis")
    assert br.decide(cx, EMAIL, ids["Levothyroxine 50 mcg"], "reject")
    assert not br.decide(cx, EMAIL, ids["Hashimotos"], "delete")
    assert not br.decide(cx, "other.test@example.com", ids["Hashimotos"], "reject")
    f = br.findings(cx, EMAIL)
    assert f["pending"] == [] and [x["text"] for x in f["approved"]] == ["Hashimoto thyroiditis"]
    assert br.approved_diagnoses(cx, EMAIL) == ["Hashimoto thyroiditis"]


# --- the routes -------------------------------------------------------------------

from biofield_local_app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


@pytest.fixture
def e4l(tmp_path):
    p = tmp_path / "e4l.db"
    cx = sqlite3.connect(str(p))
    cx.executescript(f"""CREATE TABLE e4l_clients(client_id INTEGER, name TEXT, email TEXT);
        INSERT INTO e4l_clients VALUES(1,'Pat Testerson','{EMAIL}');
        INSERT INTO e4l_clients VALUES(2,'Lee Shared','home.test@example.com');
        INSERT INTO e4l_clients VALUES(3,'Rex Shared','home.test@example.com');""")
    cx.commit(); cx.close()
    return str(p)


def _app(tmp_path, e4l, reader):
    db = str(tmp_path / "chat.db")
    app = create_app(db, scan_lookup=lambda e: _NONE, fetch_profile=lambda e: {},
                     fetch_recent_comms=lambda e: {}, e4l_db=e4l,
                     interpret_complete=lambda s, u: json.dumps({"stresses": []}),
                     client_gmail=lambda cx, e: {"text": "", "held": 0, "error": None},
                     records_reader=reader)
    return app.test_client(), db


def _new(c, email=EMAIL):
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Pat Testerson", "email": email,
                                          "date": "2026-10-16"})
    return tid


def test_read_then_approve_a_diagnosis_puts_it_in_the_summary(tmp_path, e4l):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}

    def reader(cx, email):
        bg.ensure_table(cx)
        cx.execute("INSERT OR IGNORE INTO client_gmail_messages(message_id, client_email, "
                   "sent_at, subject, body, attachments, kept, fetched_at) "
                   "VALUES('m1',?,?,?,'',?,1,'now')",
                   (email, "2026-10-01", "Labs", json.dumps(
                       [{"filename": "labs.pdf", "mime": "application/pdf", "text": ""}])))
        return br.read_records(cx, email, FakeGmail(files), _model(
            [{"kind": "diagnosis", "text": "Hashimoto thyroiditis", "page": 1}]),
            extract=_fake_extract)
    c, db = _app(tmp_path, e4l, reader)
    tid = _new(c)
    assert c.post(f"/author/{tid}/records/read").get_json()["ok"]
    j = c.get(f"/author/{tid}/records").get_json()
    assert j["ok"] and len(j["pending"]) == 1 and j["unread"] == 0
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert "Hashimoto thyroiditis" not in page and "recordFindings" in page
    fid = j["pending"][0]["id"]
    assert c.post(f"/author/{tid}/records/{fid}", json={"decision": "approve"}).status_code == 200
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert "Hashimoto thyroiditis" in page
    assert c.get(f"/author/{tid}/records").get_json()["approved"][0]["text"] == \
        "Hashimoto thyroiditis"


def test_a_shared_address_is_never_read(tmp_path, e4l):
    called = []
    c, _db = _app(tmp_path, e4l, lambda cx, e: called.append(e) or {})
    tid = _new(c, "home.test@example.com")
    j = c.post(f"/author/{tid}/records/read").get_json()
    assert not j["ok"] and called == []
    assert c.get(f"/author/{tid}/records").get_json()["held"] is True


def test_no_reader_says_so(tmp_path, e4l):
    c, _db = _app(tmp_path, e4l, None)
    tid = _new(c)
    j = c.post(f"/author/{tid}/records/read").get_json()
    assert not j["ok"] and "no Gmail token" in j["error"]


def test_the_real_reader_is_never_wired_under_pytest(tmp_path, e4l, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(bg, "TOKEN", str(tmp_path / "token.json"))
    (tmp_path / "token.json").write_text("{}")
    c, _db = _app(tmp_path, e4l, None)
    tid = _new(c)
    assert c.get(f"/author/{tid}/records").get_json()["reader"] is False


def test_mail_from_another_sender_is_never_read_as_a_record(tmp_path):
    files = {"m1": {"theirs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    cx.execute("UPDATE client_gmail_messages SET kept=0")
    cx.commit()
    assert br.unread(cx, EMAIL) == []
