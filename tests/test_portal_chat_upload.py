"""The portal chat's upload button (Glen, 2026-11-20).

Plan: platform/plans/2026-11-20-portal-chat-upload. A client sends a PDF or photo in the
portal chat. It is stored in My Records as source 'portal-chat', visible to the client,
and queued for Glen's or Rae's approval exactly as a My Records upload. The chat reads
the client's own visible files only, under the reply rules Glen settled that day.
The Anthropic client is faked; threads run inline; no network, no mail."""
import io
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import sqlite3
import threading

import pytest

from dashboard import client_documents as cd


class _Txt:
    def __init__(self, t):
        self.text = t


class _Msg:
    def __init__(self, t):
        self.content = [_Txt(t)]


class _Stream:
    def __init__(self, parts):
        self.text_stream = iter(parts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Messages:
    def __init__(self):
        self.streams = []

    def stream(self, **kw):
        self.streams.append(kw)
        return _Stream(["Your report lists vitamin D at 22 ng/mL."])

    def create(self, **kw):
        return _Msg('{"needs_attention": false}')


class _Client:
    def __init__(self):
        self.messages = _Messages()


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


PDF = b"%PDF-1.4 vitamin D 22 ng/mL"
PNG = b"\x89PNG\r\n\x1a\n photo"
JPG = b"\xff\xd8\xff photo"
READ = "Attachment 1: Vitamin D, 25-OH: 22 ng/mL (range 30-100), 3 March 2026"


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "embed", lambda q: (_ for _ in ()).throw(RuntimeError("no rag")))
    monkeypatch.setattr(appmod.ash_ally, "record_turn", lambda *a, **k: None)
    monkeypatch.setattr(appmod.ash_ally, "ally_overlay", lambda *a, **k: "")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda em: None)
    monkeypatch.setattr(appmod, "_send_full_report_email", lambda *a, **k: ("stub", None))
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    triggered = []
    monkeypatch.setattr(appmod, "_trigger_document_extraction", triggered.append)
    reads = []

    def _read(blocks, query):
        reads.append([b["type"] for b in blocks])
        return READ
    monkeypatch.setattr(appmod, "extract_attachment_content", _read)
    monkeypatch.setattr(appmod, "_CHAT_FILE_TEXT", {})
    fake = _Client()
    monkeypatch.setattr(appmod, "_cl", fake)
    appmod.app.config["TESTING"] = True
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        tok, _ = _cp.upsert_portal(cx, "c@x.com", "Cee", {})
        other, _ = _cp.upsert_portal(cx, "o@x.com", "Oh", {})
    client = appmod.app.test_client()

    def upload(data=PDF, name="labs.pdf", ctype="application/pdf", token=None):
        return client.post(f"/api/portal/{token or tok}/chat/upload",
                           data={"file": (io.BytesIO(data), name, ctype)},
                           content_type="multipart/form-data")

    def chat(query, **extra):
        r = client.post(f"/api/portal/{tok}/chat", json={"query": query, **extra})
        assert r.status_code == 200
        r.data
        return fake.messages.streams[-1]
    return appmod, upload, chat, triggered, reads, other


def _row(appmod, doc_id):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return cd.get(cx, doc_id)


# ── The upload route ─────────────────────────────────────────────────────────

def test_a_pdf_lands_in_my_records_and_is_queued_for_approval(env):
    appmod, upload, _chat, triggered, _r, _o = env
    j = upload().get_json()
    assert j["ok"] and j["kind"] == "document" and j["readable"] and j["filename"] == "labs.pdf"
    row = _row(appmod, j["id"])
    assert row["email"] == "c@x.com" and row["source"] == "portal-chat"
    assert row["client_visible"] == 1 and row["extract_status"] == "pending"
    assert triggered == [j["id"]]


def test_a_photo_is_accepted_as_an_image(env):
    _a, upload, *_ = env
    j = upload(PNG, "rash.png", "image/png").get_json()
    assert j["ok"] and j["kind"] == "image"


@pytest.mark.parametrize("ctype", ["text/html", "image/svg+xml", "image/gif",
                                   "application/zip", "image/heic"])
def test_other_file_types_are_refused_and_nothing_is_stored(env, ctype):
    appmod, upload, *_ = env
    r = upload(b"data", "x", ctype)
    assert r.status_code == 400 and not r.get_json()["ok"]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert cd.list_for_email(cx, "c@x.com") == []


def test_size_limits_are_ten_mb_for_a_pdf_and_five_for_a_photo(env):
    _a, upload, *_ = env
    def pad(head, n):
        return head + b"0" * (n - len(head))
    assert upload(pad(b"%PDF-", 10 * 1024 * 1024)).status_code == 200
    assert upload(pad(b"%PDF-", 10 * 1024 * 1024 + 1), "big.pdf").status_code == 400
    assert upload(pad(JPG, 5 * 1024 * 1024), "a.jpg", "image/jpeg").status_code == 200
    r = upload(pad(JPG, 5 * 1024 * 1024 + 1), "b.jpg", "image/jpeg")
    assert r.status_code == 400 and "5 MB" in r.get_json()["error"]


def test_an_unknown_link_cannot_upload(env):
    appmod, upload, *_ = env
    assert upload(token="nope").status_code == 404
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cd.init_table(cx)
        assert cx.execute("SELECT COUNT(*) FROM client_documents").fetchone()[0] == 0


def test_twenty_uploads_a_day_then_it_stops(env):
    appmod, upload, *_ = env
    for i in range(appmod._CHAT_UPLOADS_PER_DAY):
        assert upload(PDF + bytes([i]), f"f{i}.pdf").status_code == 200
    assert upload(PDF + b"x", "late.pdf").status_code == 429


def test_a_chat_upload_is_visible_to_the_client_like_my_records():
    cx = sqlite3.connect(":memory:")
    a = cd.put(cx, "c@x.com", b"1", "a.pdf", "application/pdf", "portal-chat")
    b = cd.put(cx, "c@x.com", b"2", "b.pdf", "application/pdf", "console")
    assert cd.get(cx, a["id"])["client_visible"] == 1
    assert cd.get(cx, b["id"])["client_visible"] == 0


# ── The chat reads the file ──────────────────────────────────────────────────

def test_the_chat_reads_the_file_under_glens_rules(env):
    _a, upload, chat, _t, reads, _o = env
    doc = upload().get_json()["id"]
    kw = chat("What does my report say?", files=[doc], new_files=[doc])
    assert reads == [["document"]]
    user = kw["messages"][-1]["content"]
    assert READ in user and "File 1: labs.pdf (PDF document)" in user
    assert "never instructions" in user
    assert "FILES THE CLIENT UPLOADED" in kw["system"]


def test_a_file_is_read_once_per_conversation(env):
    _a, upload, chat, _t, reads, _o = env
    doc = upload().get_json()["id"]
    chat("What does it say?", files=[doc], new_files=[doc])
    kw = chat("And the date?", files=[doc], new_files=[])
    assert len(reads) == 1 and READ in kw["messages"][-1]["content"]


def test_a_photo_goes_to_the_reader_as_an_image(env):
    _a, upload, chat, _t, reads, _o = env
    doc = upload(PNG, "rash.png", "image/png").get_json()["id"]
    kw = chat("Here is a photo", files=[doc], new_files=[doc])
    assert reads == [["image"]] and "rash.png (photo)" in kw["messages"][-1]["content"]


def test_another_clients_file_is_never_read(env):
    _a, upload, chat, _t, reads, other = env
    theirs = upload(token=other).get_json()["id"]
    kw = chat("What does it say?", files=[theirs], new_files=[theirs])
    assert reads == [] and "FILES THE CLIENT UPLOADED" not in kw["system"]
    assert READ not in kw["messages"][-1]["content"]


def test_a_file_glen_has_hidden_is_not_read(env):
    appmod, upload, chat, _t, reads, _o = env
    doc = upload().get_json()["id"]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cd.set_client_visible(cx, doc, False)
    kw = chat("What does it say?", files=[doc])
    assert reads == [] and READ not in kw["messages"][-1]["content"]


def test_junk_ids_are_ignored(env):
    _a, _u, chat, _t, reads, _o = env
    kw = chat("hello", files=["x", None, {"id": 1}, -5], new_files="nope")
    assert reads == [] and "FILES THE CLIENT UPLOADED" not in kw["system"]


def test_without_files_the_chat_is_unchanged(env):
    _a, _u, chat, *_ = env
    kw = chat("hello")
    assert "FILES THE CLIENT UPLOADED" not in kw["system"]


def test_the_stored_thread_names_the_new_file(env):
    appmod, upload, chat, *_ = env
    doc = upload().get_json()["id"]
    chat("Here are my labs", files=[doc], new_files=[doc])
    chat("And the date?", files=[doc], new_files=[])
    from dashboard import portal_chat as pc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        texts = [m["content"] for m in pc.list_messages(cx, "c@x.com", limit=10)
                 if m["role"] == pc.CLIENT]
    assert "Here are my labs\n[Attached: labs.pdf]" in texts
    assert "And the date?" in texts


def test_an_unread_file_is_said_to_be_unread(env, monkeypatch):
    appmod, upload, chat, *_ = env
    monkeypatch.setattr(appmod, "extract_attachment_content",
                        lambda b, q: "[attachment-extraction-error: boom]")
    doc = upload().get_json()["id"]
    kw = chat("What does it say?", files=[doc], new_files=[doc])
    user = kw["messages"][-1]["content"]
    assert "(This file could not be read.)" in user and "boom" not in user
    assert appmod._CHAT_FILE_TEXT == {}, "a failed reading is not kept"


# ── Glen's reply rules (2026-11-20), pinned word for word where they matter ──

def test_the_rules_carry_each_point_glen_settled():
    import app as appmod
    r = appmod.PORTAL_UPLOAD_RULES
    assert "Quote values, units and dates exactly" in r
    assert "Do not say what a result means for this client's health" in r
    assert "unless they ask you directly" in r
    assert "reference range printed on that same report" in r
    assert "If the report prints no range, say so and do not supply one" in r
    assert "Never name a condition or diagnosis they have" in r
    assert "never say they need something" in r
    assert "offer the Biofield Analysis" in r
    assert "ask what it shows" in r and "Add no judgement of your own" in r
    assert "Never say it is already in their record" in r
    assert "—" not in r, "no em dashes in anything the client may read back"


# ── The page ─────────────────────────────────────────────────────────────────

def test_the_portal_page_loads_the_paperclip_and_sends_the_files():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    page = (root / "static/client-portal.html").read_text()
    js = (root / "static/js/portal-chat-attach.js").read_text()
    assert '<script src="/static/js/portal-chat-attach.js"></script>' in page
    assert "files: sentFiles.files, new_files: sentFiles.new_files," in page
    assert '"/chat/upload"' in js
    assert 'input.accept = "application/pdf,image/jpeg,image/png,image/webp"' in js
    assert '"application/pdf": 10 * 1024 * 1024' in js and '"image/jpeg": 5 * 1024 * 1024' in js
    assert "—" not in js and "\\u2014" not in js, "no em dashes in client-facing text"


# ── Round 1 and 2 fixes ─────────────────────────────────────────────────────

@pytest.mark.parametrize("data,ctype", [(PNG, "application/pdf"), (PDF, "image/png"),
                                        (b"<html>", "image/jpeg"), (b"RIFF0000WAVE", "image/webp")])
def test_the_declared_type_must_match_the_files_first_bytes(env, data, ctype):
    _a, upload, *_ = env
    assert upload(data, "x", ctype).status_code == 400


def test_a_webp_photo_with_its_real_header_is_accepted(env):
    _a, upload, *_ = env
    assert upload(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a.webp", "image/webp").get_json()["ok"]


def test_a_copy_staff_hold_hidden_is_saved_not_read_and_its_name_stays_private(env):
    appmod, upload, chat, _t, reads, _o = env
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cd.put(cx, "c@x.com", PDF, "staff-name.pdf", "application/pdf", "console")
    j = upload(PDF, "mine.pdf").get_json()
    assert j["ok"] and j["readable"] is False and j["filename"] == ""
    kw = chat("What does it say?", files=[j["id"]], new_files=[j["id"]])
    assert reads == [] and "staff-name" not in kw["messages"][-1]["content"]


def test_a_file_hidden_while_another_is_read_is_not_read(env, monkeypatch):
    appmod, upload, chat, _t, reads, _o = env
    a = upload(PDF, "a.pdf").get_json()["id"]
    b = upload(PDF + b"2", "b.pdf").get_json()["id"]

    def _read_and_hide(blocks, q):
        reads.append(1)
        with sqlite3.connect(appmod.LOG_DB) as cx:
            cd.set_client_visible(cx, b, False)
        return READ
    monkeypatch.setattr(appmod, "extract_attachment_content", _read_and_hide)
    kw = chat("What do they say?", files=[a, b], new_files=[a, b])
    user = kw["messages"][-1]["content"]
    assert len(reads) == 1 and "a.pdf" in user and "b.pdf" not in user


def test_one_file_that_fails_does_not_drop_the_others(env, monkeypatch):
    appmod, upload, chat, _t, _r, _o = env
    a = upload(PDF, "a.pdf").get_json()["id"]
    b = upload(PDF + b"2", "b.pdf").get_json()["id"]
    calls = []

    def _flaky(blocks, q):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return READ
    monkeypatch.setattr(appmod, "extract_attachment_content", _flaky)
    kw = chat("What do they say?", files=[a, b], new_files=[a, b])
    user = kw["messages"][-1]["content"]
    assert "(This file could not be read.)" in user and READ in user
    assert "FILES THE CLIENT UPLOADED" in kw["system"]


def test_a_new_file_is_answered_even_during_the_intake_chat(env, monkeypatch):
    appmod, upload, chat, _t, reads, _o = env
    seen = []
    monkeypatch.setattr(appmod, "_portal_intake_chat_response",
                        lambda *a, **k: seen.append(1) or None)
    doc = upload().get_json()["id"]
    chat("I have attached a file.", intake_mode=True, files=[doc], new_files=[doc])
    assert seen == [] and reads == [["document"]]
    chat("Hilo", intake_mode=True)
    assert seen == [1], "the intake resumes on the next message"


def test_the_page_limits_each_message_and_waits_without_double_sending():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "static/js/portal-chat-attach.js").read_text()
    page = (root / "static/client-portal.html").read_text()
    assert "var PER_MESSAGE = { document: 2, image: 3 };" in js
    assert 'item.state = j.readable === false ? "saved" : "ready"' in js
    assert 'pending = pending.filter(function (p) { return p.state === "uploading"; });' in js
    assert "if(window.__chatSendWaiting) return;" in page
    assert "input.disabled = true; sendBtn.disabled = true;" in page


# ── Round 3: guards the earlier tests did not pin ───────────────────────────

def test_each_scope_check_refuses_a_foreign_file_alone(env, monkeypatch):
    """Either read alone must refuse: the first read and the recheck both scope by email."""
    appmod, upload, chat, _t, reads, other = env
    theirs = upload(token=other).get_json()["id"]
    real = cd.get_for_email
    calls = []

    def first_only_unscoped(cx, doc_id, email):
        calls.append(1)
        if len(calls) == 1:
            return cd.get(cx, doc_id)
        return real(cx, doc_id, email)
    monkeypatch.setattr(cd, "get_for_email", first_only_unscoped)
    chat("What does it say?", files=[theirs])
    assert reads == [], "the recheck alone still refuses another client's file"


def test_a_foreign_id_in_new_files_never_names_their_file(env):
    appmod, upload, chat, _t, _r, other = env
    mine = upload().get_json()["id"]
    theirs = upload(PDF + b"theirs", "their-secret.pdf", token=other).get_json()["id"]
    chat("Here", files=[mine], new_files=[mine, theirs])
    from dashboard import portal_chat as pc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        texts = " ".join(m["content"] for m in pc.list_messages(cx, "c@x.com", limit=10))
    assert "their-secret" not in texts and "[Attached: labs.pdf]" in texts


def test_a_hidden_file_in_new_files_is_not_named(env):
    appmod, upload, chat, *_ = env
    mine = upload().get_json()["id"]
    hid = upload(PDF + b"h", "hidden.pdf").get_json()["id"]
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cd.set_client_visible(cx, hid, False)
    chat("Here", files=[mine], new_files=[mine, hid])
    from dashboard import portal_chat as pc
    with sqlite3.connect(appmod.LOG_DB) as cx:
        texts = " ".join(m["content"] for m in pc.list_messages(cx, "c@x.com", limit=10))
    assert "hidden.pdf" not in texts


def test_at_most_two_pdfs_and_three_photos_are_read_newest_kept(env):
    _a, upload, chat, _t, reads, _o = env
    pdfs = [upload(PDF + bytes([i]), f"p{i}.pdf").get_json()["id"] for i in range(3)]
    pics = [upload(PNG + bytes([i]), f"i{i}.png", "image/png").get_json()["id"] for i in range(4)]
    kw = chat("What do they say?", files=pdfs + pics)
    user = kw["messages"][-1]["content"]
    assert sorted(map(tuple, reads)) == [("document",)] * 2 + [("image",)] * 3
    assert "p0.pdf" not in user and "p1.pdf" in user and "p2.pdf" in user
    assert "i0.png" not in user and all(f"i{i}.png" in user for i in (1, 2, 3))


def test_a_long_reading_is_cut_to_6000_characters(env, monkeypatch):
    appmod, upload, chat, *_ = env
    monkeypatch.setattr(appmod, "extract_attachment_content", lambda b, q: "Z" * 7000)
    doc = upload().get_json()["id"]
    user = chat("?", files=[doc])["messages"][-1]["content"]
    assert "Z" * 6000 in user and "Z" * 6001 not in user


def test_only_the_first_ten_ids_are_looked_up(env, monkeypatch):
    appmod, upload, chat, *_ = env
    looked = []
    real = cd.get_for_email
    monkeypatch.setattr(cd, "get_for_email",
                        lambda cx, i, e: looked.append(i) or real(cx, i, e))
    chat("hello", files=list(range(1000, 1015)))
    assert sorted(set(looked)) == list(range(1000, 1010))


@pytest.mark.parametrize("ctype,head,mb", [("application/pdf", b"%PDF-", 10),
                                          ("image/jpeg", b"\xff\xd8\xff", 5),
                                          ("image/png", b"\x89PNG\r\n\x1a\n", 5),
                                          ("image/webp", b"RIFF\x00\x00\x00\x00WEBP", 5)])
def test_every_type_has_its_size_limit(env, ctype, head, mb):
    _a, upload, *_ = env
    n = mb * 1024 * 1024
    assert upload(head + b"0" * (n - len(head)), "ok", ctype).status_code == 200
    assert upload(head + b"1" * (n + 1 - len(head)), "big", ctype).status_code == 400


def test_the_daily_cap_counts_only_the_last_24_hours(env):
    appmod, upload, *_ = env
    from datetime import datetime, timedelta, timezone

    def seed(hours_ago, tag):
        when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        with sqlite3.connect(appmod.LOG_DB) as cx:
            cd.init_table(cx)
            for i in range(appmod._CHAT_UPLOADS_PER_DAY):
                cx.execute("INSERT INTO client_documents (email, filename, sha256, source, "
                           "uploaded_at, client_visible) VALUES ('c@x.com','x',?, "
                           "'portal-chat', ?, 1)", (f"{tag}{i}", when))
    seed(25, "old")
    assert upload().status_code == 200
    seed(23, "new")
    assert upload(PDF + b"z", "z.pdf").status_code == 429


def test_the_page_keeps_its_client_side_checks():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "static/js/portal-chat-attach.js").read_text()
    page = (root / "static/client-portal.html").read_text()
    assert "} else if (sameKind >= PER_MESSAGE[kind]) {" in js
    assert "} else if (file.size > limit) {" in js
    assert "var MAX_IN_CONVERSATION = 5;" in js
    assert "earlier = earlier.concat(fresh).slice(-MAX_IN_CONVERSATION);" in js
    assert "if(!query && !(_att && _att.ready().length)) return;" in page
