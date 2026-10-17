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


def _fake_extract(mime, name, data, **_k):
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
    # A page the model was not shown is no citation: that finding is dropped.
    assert out == [{"kind": "diagnosis", "text": "Hashimoto thyroiditis", "page": 1,
                    "date": "2026-03-04"}]
    for bad in ("not json", "{}", '{"findings": "x"}', '[]',
                json.dumps({"findings": [{"kind": "advice", "text": "rest", "page": 1}]})):
        with pytest.raises(br.ModelReplyError):
            br.ask(lambda s, u, b=bad: b, ["text"], "x.pdf")
    assert br.ask(_model([]), ["text"], "x.pdf") == []
    assert br.ask(_model(raw), ["", "  "], "blank.pdf") == []


def test_records_are_read_once_into_pending_findings(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"TSH 4.9 high")}}
    cx = _cx(tmp_path, files)
    svc, seen = FakeGmail(files), []
    got = br.read_records(cx, EMAIL, svc, _model(
        [{"kind": "result", "text": "TSH 4.9 high", "page": 1}], seen), extract=_fake_extract)
    assert got == {"read": 1, "failed": 0, "not_fetched": 0, "added": 1, "busy": False,
                   "remaining": 0}
    assert "[page 1]\nTSH 4.9 high" in seen[0]
    f = br.findings(cx, EMAIL)
    assert [x["text"] for x in f["pending"]] == ["TSH 4.9 high"] and f["approved"] == []
    assert f["pending"][0]["filename"] == "labs.pdf" and f["pending"][0]["page"] == 1
    assert f["pending"][0]["subject"] == "My labs"
    svc.calls.clear()
    assert br.read_records(cx, EMAIL, svc, _model([]), extract=_fake_extract)["read"] == 0
    assert svc.calls == []


def test_gmail_failing_is_retried_later_then_named(tmp_path, monkeypatch):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    got = br.read_records(cx, EMAIL, FakeGmail(files, fail=True), _model([]),
                          extract=_fake_extract)
    # Not again within RETRY_AFTER: one outage must not spend every attempt.
    assert got["not_fetched"] == 1 and got["remaining"] == 0
    monkeypatch.setattr(br, "RETRY_AFTER", -5)
    for _ in range(br.ATTEMPTS - 1):
        br.read_records(cx, EMAIL, FakeGmail(files, fail=True), _model([]),
                        extract=_fake_extract)
    assert br.findings(cx, EMAIL)["failed"] == ["labs.pdf"] and br.unread(cx, EMAIL) == []


def test_a_deleted_email_is_named_at_once(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)

    class Gone(FakeGmail):
        def get(self, userId, id, format=None, messageId=None):
            import types
            e = Exception("Not Found")
            e.resp = types.SimpleNamespace(status=404)
            raise e
    got = br.read_records(cx, EMAIL, Gone(files), _model([]), extract=_fake_extract)
    assert got["failed"] == 1 and br.findings(cx, EMAIL)["failed"] == ["labs.pdf"]


def test_empty_files_of_every_kind_are_not_nothing_found():
    for mime, name, data in (("text/plain", "a.txt", b"  "),):
        with pytest.raises(br.NoText):
            br.extract_pages(mime, name, data)


def test_retry_reads_failed_records_again(tmp_path, e4l):
    c, db = _app(tmp_path, e4l, lambda cx, e: {})
    tid = _new(c)
    cx = sqlite3.connect(db)
    br.ensure_tables(cx)
    cx.execute("INSERT INTO client_record_reads VALUES(?, 'm', 0, 'x.pdf', 'now', 0, 'E', 3, NULL)",
               (EMAIL,))
    cx.commit()
    assert c.get(f"/author/{tid}/records").get_json()["failed"] == ["x.pdf"]
    assert c.post(f"/author/{tid}/records/retry").get_json()["reset"] == 1
    assert c.get(f"/author/{tid}/records").get_json()["failed"] == []


def test_an_unreadable_record_is_named_and_not_read_again(tmp_path):
    files = {"m1": {"broken.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)

    def boom(*a, **k):
        raise ValueError("corrupt")
    # Tried ATTEMPTS times, then named and left alone.
    br.RETRY_AFTER, _keep = -5, br.RETRY_AFTER
    for n in range(br.ATTEMPTS):
        assert br.findings(cx, EMAIL)["failed"] == []
        got = br.read_records(cx, EMAIL, FakeGmail(files), _model([]), extract=boom)
        assert got["failed"] == 1
    br.RETRY_AFTER = _keep
    assert got["remaining"] == 0
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
    assert not br.decide(cx, EMAIL, ids["Hashimotos"], "withdraw")   # not approved yet
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



def test_two_files_with_one_name_are_two_records(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat.db"))
    bg.ensure_table(cx)
    cx.execute("INSERT INTO client_gmail_messages(message_id, client_email, sent_at, subject, "
               "body, attachments, kept, fetched_at) VALUES('m1',?,?,?,'',?,1,'now')",
               (EMAIL, "2026-10-01", "two", json.dumps(
                   [{"filename": "report.pdf", "mime": "application/pdf", "text": ""}] * 2)))
    cx.commit()

    class Two(FakeGmail):
        def get(self, userId, id, format=None, messageId=None):
            if messageId is not None:
                return _Exec({"data": _b(b"first" if id == "a0" else b"second")})
            parts = [{"mimeType": "application/pdf", "filename": "report.pdf",
                      "body": {"attachmentId": f"a{i}", "size": 5}} for i in range(2)]
            return _Exec({"id": id, "payload": {"mimeType": "multipart/mixed", "parts": parts}})
    seen = []
    br.read_records(cx, EMAIL, Two({}), _model([], seen), extract=_fake_extract)
    assert [u.split("[page 1]\n")[1].strip() for u in seen] == ["first", "second"]


def test_a_missing_attachment_is_recorded_once(tmp_path):
    files = {"m1": {"gone.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    svc = FakeGmail({"m1": {}})
    got = br.read_records(cx, EMAIL, svc, _model([]), extract=_fake_extract)
    assert got["failed"] == 1 and got["remaining"] == 0


def test_a_long_record_is_read_in_full_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "ASK_MAX", 50)
    seen = []
    br.ask(_model([], seen), ["a" * 40, "b" * 40, "c" * 200], "long.pdf")
    assert len(seen) == 3 and "[page 3]" in seen[2] and len(seen[2]) < 200


def test_a_blank_scan_is_a_failure_not_nothing_found():
    with pytest.raises(br.NoText):
        br.extract_pages("image/png", "x.png", b"img", ocr=lambda d: "  ")


def test_a_scan_under_a_typed_header_is_still_read(monkeypatch):
    pytest.importorskip("pypdf")
    import pypdf
    class Page:
        images = [object()]
        def extract_text(self):
            return "Hilo Clinic letterhead, 123 Main Street, phone 555"
    class Reader:
        def __init__(self, _b):
            self.pages = [Page()]
    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    pages = br.extract_pages("application/pdf", "s.pdf", b"x", ocr=lambda d: "TSH 4.9 high",
                             rasterise=lambda d, p: b"png")
    assert "letterhead" in pages[0] and "TSH 4.9 high" in pages[0]


def test_a_long_scan_stops_at_its_deadline_and_says_so(monkeypatch):
    pytest.importorskip("pypdf")
    import pypdf
    class Page:
        images = []
        def extract_text(self):
            return ""
    class Reader:
        def __init__(self, _b):
            self.pages = [Page() for _ in range(10)]
    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    ticks = iter(range(0, 1000, 10))
    pages = br.extract_pages("application/pdf", "s.pdf", b"x", ocr=lambda d: "words",
                             rasterise=lambda d, p: b"png", deadline=25,
                             clock=lambda: next(ticks))
    assert len(pages) == 3 and pages.total == 10


def test_a_second_read_while_one_runs_is_refused(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    lock = br._lock_for(EMAIL)
    lock.acquire()
    try:
        assert br.read_records(cx, EMAIL, FakeGmail(files), _model([]),
                               extract=_fake_extract)["busy"] is True
    finally:
        lock.release()


def test_a_withdrawn_diagnosis_leaves_the_summary(tmp_path):
    files = {"m1": {"labs.pdf": ("application/pdf", b"x")}}
    cx = _cx(tmp_path, files)
    br.read_records(cx, EMAIL, FakeGmail(files), _model(
        [{"kind": "diagnosis", "text": "Anaemia", "page": 1}]), extract=_fake_extract)
    fid = br.findings(cx, EMAIL)["pending"][0]["id"]
    br.decide(cx, EMAIL, fid, "approve")
    assert br.approved_diagnoses(cx, EMAIL) == ["Anaemia"]
    assert br.decide(cx, EMAIL, fid, "withdraw")
    assert br.approved_diagnoses(cx, EMAIL) == [] and br.findings(cx, EMAIL)["approved"] == []


def test_a_photo_finding_cites_no_page(tmp_path):
    files = {"m1": {"scan.jpg": ("image/jpeg", b"x")}}
    cx = _cx(tmp_path, files)
    br.read_records(cx, EMAIL, FakeGmail(files), _model(
        [{"kind": "result", "text": "A1c 6.1", "page": 1}]), extract=_fake_extract)
    assert br.findings(cx, EMAIL)["pending"][0]["page"] is None


def test_the_page_escapes_quotes_in_model_text():
    import subprocess
    from dashboard.biofield_report_html import _RECORDS_HTML
    import os
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI for this test")
        pytest.skip("needs node")
    js = _RECORDS_HTML.split("<script>")[1].split("</script>")[0]
    fn = js[js.index("function esc("):js.index("function src(")]
    hostile = 'x" autofocus onfocus="alert(1)\' <b>'
    out = subprocess.run(["node", "-e", fn + "process.stdout.write(esc(process.argv[1]))",
                          hostile], capture_output=True, text=True).stdout
    assert '"' not in out and "'" not in out and "<" not in out and "&quot;" in out


def test_findings_on_an_address_that_is_shared_now_are_hidden(tmp_path, e4l):
    def reader(cx, email):
        return {}
    c, db = _app(tmp_path, e4l, reader)
    tid = _new(c)
    cx = sqlite3.connect(db)
    br.ensure_tables(cx)
    cx.execute("INSERT INTO client_record_findings(client_email, message_id, part, filename, "
               "page, kind, text, status, created_at) VALUES(?,'m',0,'f.pdf',1,'diagnosis',"
               "'Gout','approved','now')", (EMAIL,))
    cx.commit()
    assert "Gout" in c.get(f"/author/{tid}").get_data(as_text=True)
    s2 = sqlite3.connect(e4l)
    s2.execute("INSERT INTO e4l_clients VALUES(9,'Someone Else',?)", (EMAIL,)); s2.commit()
    assert "Gout" not in c.get(f"/author/{tid}").get_data(as_text=True)
    j = c.get(f"/author/{tid}/records").get_json()
    assert j["held"] is True and j["approved"] == []



def test_the_panel_renders_hostile_record_text_inert():
    """Runs the panel's own script in node against a hostile reply."""
    import os
    import subprocess
    from dashboard.biofield_report_html import _RECORDS_HTML
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI for this test")
        pytest.skip("needs node")
    js = _RECORDS_HTML.split("<script>")[1].split("</script>")[0]
    bad = '"><img src=x onerror=alert(1)>\' onfocus=\'alert(2)'
    reply = {"ok": True, "held": False, "unread": 0, "reader": False, "read_count": 1,
             "failed": [bad], "notes": [bad],
             "pending": [{"id": 1, "kind": "result", "kind_label": bad, "text": bad,
                          "date": bad, "filename": bad, "page": 2, "email_date": bad,
                          "subject": bad}],
             "approved": [{"id": 2, "kind": "diagnosis", "kind_label": "Diagnosis",
                           "text": bad, "date": "", "filename": bad, "page": None,
                           "email_date": bad, "subject": bad}]}
    harness = ("var box={innerHTML:'',addEventListener:function(){}},"
               "pbox={innerHTML:'',addEventListener:function(){}};"
               "global.document={getElementById:function(id){"
               "return id==='recordProposals'?pbox:box}};"
               "global.location={pathname:'/author/a1'};"
               "global.fetch=async function(){return {json:async function(){return "
               + json.dumps(reply) + "}}};" + js +
               ";setTimeout(function(){process.stdout.write(pbox.innerHTML+'|||'+box.innerHTML)},50);")
    html = subprocess.run(["node", "-e", harness], capture_output=True, text=True,
                          timeout=20).stdout
    proposed, listed = html.split("|||")
    # One review place: pending findings sit in the proposals box (Glen: "one box").
    assert "Proposed from records" in proposed and ">Add<" in proposed
    assert "Not relevant" in proposed and "Proposed from records" not in listed
    assert "From records" in listed
    assert bad not in html and "<img" not in html and "' onfocus='" not in html
    assert html.count("&lt;img src=x") >= 8      # each field shown, escaped



def test_a_photo_with_no_text_is_noted_not_failed(tmp_path):
    files = {"m1": {"Josie 1.jpeg": ("image/jpeg", b"x")}}
    cx = _cx(tmp_path, files)

    def blank(mime, name, data, **k):
        raise br.NoText("none")
    got = br.read_records(cx, EMAIL, FakeGmail(files), _model([]), extract=blank)
    f = br.findings(cx, EMAIL)
    assert got["failed"] == 0 and f["failed"] == []
    assert f["notes"] == ["Josie 1.jpeg: a photo with no text in it"]


def test_the_proposals_box_holds_both_sources():
    from dashboard.biofield_report_html import _RECORDS_HTML
    import dashboard.biofield_report_html as h
    src = open(h.__file__).read()
    assert "<div id=clinicalProposals></div><div id=recordProposals></div>" in src
    assert "getElementById('recordProposals')" in _RECORDS_HTML
