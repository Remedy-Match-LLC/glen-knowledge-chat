"""Findings read from a client's clinical records, for Glen to review (2026-10-16).

Glen: "Is the programming set to analyze her clinical reports (the 7 pdfs) and add
important findings to clinical summary?" His choices: review first, then an approved
diagnosis becomes a Clinical summary row and every approved finding is listed under the
summary with its email and page.

The records are the attachments of mail already kept by biofield_gmail (the client's own
address only, never a held household address). Each attachment is fetched again from
Gmail when it is read here: no file is kept on disk. Scanned pages and photos are read
by Tesseract on this Mac. The words are then read by the same model that reads comms.
Nothing reaches the summary until Glen approves it.
"""
import json
import re
import subprocess
import threading
from datetime import datetime, timezone

KINDS = ("diagnosis", "medication", "result", "procedure")
KIND_LABEL = {"diagnosis": "Diagnosis", "medication": "Medication",
              "result": "Test result", "procedure": "Procedure"}
PAGES_MAX = 30          # pages read per record
OCR_MIN_CHARS = 40      # a PDF page with less text than this is treated as a scan
ASK_MAX = 30_000        # characters per model call; a longer record takes several
ATTEMPTS = 3            # a model or OCR failure is tried this many times in all
OCR_TIMEOUT = 30        # seconds per page
BUDGET = 240            # seconds per read request; the rest is read on the next one
FILE_MAX = 10_000_000   # bytes

_IMAGE = ("image/jpeg", "image/png", "image/tiff", "image/gif", "image/bmp", "image/webp")
_TEXTUTIL = {".rtf": "rtf", ".doc": "doc", ".docx": "docx", ".odt": "odt"}


def ensure_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS client_record_findings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        client_email  TEXT NOT NULL,
        message_id    TEXT NOT NULL,
        part          INTEGER NOT NULL,
        filename      TEXT NOT NULL,
        page          INTEGER NOT NULL,
        kind          TEXT NOT NULL,
        text          TEXT NOT NULL,
        finding_date  TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        created_at    TEXT NOT NULL,
        decided_at    TEXT,
        UNIQUE (client_email, message_id, part, page, kind, text))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS client_record_reads (
        client_email TEXT NOT NULL,
        message_id   TEXT NOT NULL,
        part         INTEGER NOT NULL,
        filename     TEXT NOT NULL,
        read_at      TEXT NOT NULL,
        pages        INTEGER,
        error        TEXT,
        attempts     INTEGER NOT NULL DEFAULT 0,
        note         TEXT,
        PRIMARY KEY (client_email, message_id, part))""")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_kind(mime, filename):
    """"pdf", "image", "textutil" or "text" for a record this can read, else ""."""
    mime, name = (mime or "").lower(), (filename or "").lower()
    if mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if mime in _IMAGE or name.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
        return "image"
    if any(name.endswith(ext) for ext in _TEXTUTIL) or "rtf" in mime or "msword" in mime \
            or "wordprocessingml" in mime:
        return "textutil"
    if mime == "text/plain" or name.endswith(".txt"):
        return "text"
    return ""


def _run(cmd, data, timeout=OCR_TIMEOUT):
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed")
    return r.stdout


def ocr_image(data):
    """Tesseract, from memory to memory."""
    return _run(["tesseract", "stdin", "stdout"], data).decode("utf-8", "replace")


def pdf_page_png(data, page):
    return _run(["pdftoppm", "-png", "-r", "200", "-f", str(page), "-l", str(page),
                 "-singlefile", "-"], data)


class _Pages(list):
    """Page texts, plus how many pages the record has in all."""
    total = None


class NoText(ValueError):
    """Every page came back empty: a blank or unreadable scan, not "nothing found"."""


def extract_pages(mime, filename, data, *, ocr=ocr_image, rasterise=pdf_page_png,
                  deadline=None, clock=None):
    """One text per page. Raises when the record cannot be read at all, or NoText when
    no page has any words. Past `deadline` the rest of the pages are left, and the
    result's `total` says how many there were."""
    kind = record_kind(mime, filename)
    if kind == "pdf":
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        pages = _Pages()
        pages.total = len(reader.pages)
        for i, page in enumerate(reader.pages[:PAGES_MAX], 1):
            if deadline is not None and clock and clock() > deadline:
                break
            text = (page.extract_text() or "").strip()
            try:
                has_image = bool(page.images)
            except Exception:
                has_image = False
            # A scan, or a scan under a typed header (review round 1).
            if len(text) < OCR_MIN_CHARS or (has_image and len(text) < 800):
                scanned = ocr(rasterise(data, i)).strip()
                text = scanned if len(text) < OCR_MIN_CHARS else (text + "\n" + scanned)
            pages.append(text)
        if not any(p.strip() for p in pages) and pages:
            raise NoText("no words on any page")
        return pages
    if kind == "image":
        text = ocr(data).strip()
        if not text:
            raise NoText("no words in the image")
        return [text]
    if kind == "textutil":
        ext = next((v for k, v in _TEXTUTIL.items() if (filename or "").lower().endswith(k)),
                   "rtf")
        return [_run(["textutil", "-convert", "txt", "-stdin", "-stdout", "-format", ext],
                     data).decode("utf-8", "replace").strip()]
    if kind == "text":
        return [data.decode("utf-8", "replace").strip()]
    raise ValueError("not a record this can read")


SYSTEM = """You read one clinical record a client sent to her practitioner.
List only what the record itself states about the person it is about:
- diagnosis: a diagnosis or named condition
- medication: a medication or supplement with its dose if given
- result: a test result, with its value, unit and flag if given
- procedure: a procedure, surgery or imaging study
Never infer, never add advice, never include family history. Keep each finding under
160 characters, in the record's own words where possible. Give the page it is on and
its date (YYYY-MM-DD) when the record states one.
If the record is not clinical, return no findings.
Reply as JSON: {"findings": [{"kind": "...", "text": "...", "page": 1, "date": "..."}]}"""


class ModelReplyError(ValueError):
    """The model answered, but not in the shape asked for."""


def _chunks(pages):
    """(first page number, [texts]) groups that each fit one model call. A page longer
    than a call is cut, never dropped."""
    group, used, first = [], 0, 1
    for i, text in enumerate(pages, 1):
        text = text[:ASK_MAX]
        if group and used + len(text) > ASK_MAX:
            yield first, group
            group, used, first = [], 0, i
        group.append(text)
        used += len(text)
    if group:
        yield first, group


def _ask_once(complete, first, texts, filename):
    body = "".join(f"[page {first + n}]\n{t}\n" for n, t in enumerate(texts))
    raw = complete(SYSTEM, f"Record: {filename}\n\n" + body)
    try:
        reply = json.loads(raw)
    except (TypeError, ValueError):
        raise ModelReplyError("the model's reply was not JSON")
    found = reply.get("findings") if isinstance(reply, dict) else None
    if not isinstance(found, list):
        raise ModelReplyError("the model's reply had no findings list")
    out = []
    for f in found:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "").strip().lower()
        text = " ".join(str(f.get("text") or "").split())
        try:
            page = int(f.get("page"))
        except (TypeError, ValueError):
            page = 0
        # Every finding cites a page the model was actually shown (review round 2).
        if kind not in KINDS or not (3 <= len(text) <= 200) \
                or not (first <= page < first + len(texts)):
            continue
        date = str(f.get("date") or "").strip()
        date = date if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", date) else ""
        out.append({"kind": kind, "text": text, "page": page, "date": date})
    if found and not out:
        raise ModelReplyError("none of the model's findings could be used")
    return out


def ask(complete, pages, filename):
    """The model's findings for one record, every page read, validated."""
    if not any((p or "").strip() for p in pages):
        return []
    out = []
    for first, texts in _chunks(list(pages)):
        if any(t.strip() for t in texts):
            out.extend(_ask_once(complete, first, texts, filename))
    return out


def unread(cx, email):
    """Records in this client's kept mail not read yet, or failed fewer than ATTEMPTS
    times. Each is addressed by its place among the message's attachments, so two
    files with one name are two records (review round 2)."""
    ensure_tables(cx)
    done = {(r[0], r[1]) for r in cx.execute(
        "SELECT message_id, part FROM client_record_reads WHERE client_email=? AND "
        "(error IS NULL OR attempts >= ?)", (email, ATTEMPTS))}
    try:
        rows = cx.execute("SELECT message_id, attachments FROM client_gmail_messages "
                          "WHERE client_email=? AND kept=1 ORDER BY sent_at DESC",
                          (email,)).fetchall()
    except Exception:
        return []
    out = []
    for mid, atts in rows:
        for part, a in enumerate(json.loads(atts or "[]")):
            name = a.get("filename") or ""
            if name and record_kind(a.get("mime"), name) and (mid, part) not in done:
                out.append({"message_id": mid, "part": part, "filename": name,
                            "mime": a.get("mime")})
    return out


class RecordGone(LookupError):
    """The attachment is not there, or too big: retrying will not help."""


def _named_parts(payload):
    """The parts biofield_gmail lists as attachments, in the same order."""
    from dashboard.biofield_gmail import _walk
    for p in _walk(payload or {}):
        if (p.get("mimeType") or "").lower().startswith("message/") or p.get("filename"):
            yield p


def _attachment_bytes(service, message_id, part, filename):
    """Fetch one attachment again. Gmail attachment ids are not stable, so the part is
    found by its place in a fresh copy of the message, and its name must still match."""
    from dashboard.biofield_gmail import _b64
    users = service.users()
    msg = users.messages().get(userId="me", id=message_id, format="full").execute()
    parts = list(_named_parts(msg.get("payload")))
    if part >= len(parts) or (parts[part].get("filename") or "") != filename:
        raise RecordGone("attachment not found")
    body = parts[part].get("body") or {}
    if int(body.get("size") or 0) > FILE_MAX:
        raise RecordGone("over 10 MB")
    if body.get("data"):
        return _b64(body["data"])
    got = users.messages().attachments().get(
        userId="me", messageId=message_id, id=body.get("attachmentId")).execute()
    return _b64(got.get("data"))


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(email):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(email, threading.Lock())


def read_records(cx, email, service, complete, *, budget=BUDGET, clock=None,
                 extract=extract_pages):
    """Read each unread record, store its findings as pending. Returns counts.

    One read per client at a time: a second page load gets busy=True instead of
    reading the same records twice (review round 2). Gmail failing is retried next
    time and counted as not_fetched. A record that will never come back (gone, too
    big) is recorded at once. A model or OCR failure is tried ATTEMPTS times."""
    import time
    lock = _lock_for(email)
    if not lock.acquire(blocking=False):
        return {"read": 0, "failed": 0, "not_fetched": 0, "added": 0, "busy": True,
                "remaining": len(unread(cx, email))}
    try:
        clock = clock or time.monotonic
        deadline = clock() + budget
        ensure_tables(cx)
        done = failed = not_fetched = added = 0
        for rec in unread(cx, email):
            if clock() > deadline:
                break
            mid, part, name = rec["message_id"], rec["part"], rec["filename"]
            note, final = None, False
            try:
                data = _attachment_bytes(service, mid, part, name)
            except RecordGone as e:
                data, error, final = None, str(e), True
            except Exception:
                not_fetched += 1          # Gmail, not the record: tried again next time
                continue
            pages, found = [], []
            if data is not None:
                try:
                    pages = extract(rec["mime"], name, data, deadline=deadline + 120,
                                    clock=clock)
                    if getattr(pages, "total", None) and pages.total > len(pages):
                        note = f"pages 1 to {len(pages)} of {pages.total} were read"
                    found = ask(complete, pages, name)
                    error = None
                except NoText:
                    pages, found, error, final = [], [], "no words could be read", True
                except Exception as e:      # OCR, a broken file, or the model
                    pages, found, error = [], [], type(e).__name__
            if error:
                failed += 1
            now = _now()
            for f in found:
                cur = cx.execute(
                    "INSERT OR IGNORE INTO client_record_findings(client_email, message_id, "
                    "part, filename, page, kind, text, finding_date, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (email, mid, part, name, f["page"], f["kind"], f["text"], f["date"], now))
                added += max(0, cur.rowcount or 0)
            prev = cx.execute("SELECT attempts FROM client_record_reads WHERE client_email=? "
                              "AND message_id=? AND part=?", (email, mid, part)).fetchone()
            attempts = ATTEMPTS if final else (prev[0] if prev else 0) + 1
            cx.execute("INSERT OR REPLACE INTO client_record_reads(client_email, message_id, "
                       "part, filename, read_at, pages, error, attempts, note) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (email, mid, part, name, now, len(pages), error, attempts, note))
            cx.commit()
            done += 1
        return {"read": done, "failed": failed, "not_fetched": not_fetched, "added": added,
                "busy": False, "remaining": len(unread(cx, email))}
    finally:
        lock.release()


def findings(cx, email):
    """{"pending", "approved", "failed"} for the page, each finding with its source."""
    ensure_tables(cx)
    try:
        meta = {r[0]: (r[1] or "", r[2] or "") for r in cx.execute(
            "SELECT message_id, sent_at, subject FROM client_gmail_messages "
            "WHERE client_email=?", (email,))}
    except Exception:
        meta = {}
    out = {"pending": [], "approved": []}
    for fid, mid, name, page, kind, text, date, status in cx.execute(
            "SELECT id, message_id, filename, page, kind, text, finding_date, status "
            "FROM client_record_findings WHERE client_email=? AND status IN "
            "('pending','approved') ORDER BY finding_date DESC, id", (email,)):
        sent, subject = meta.get(mid, ("", ""))
        # Only a PDF has pages to cite; a photo or Word file is one document.
        cite = page if record_kind("", name) == "pdf" else None
        out[status].append({"id": fid, "kind": kind, "kind_label": KIND_LABEL[kind],
                            "text": text, "date": date or "", "filename": name,
                            "page": cite, "email_date": sent[:10], "subject": subject})
    out["failed"] = [r[0] for r in cx.execute(
        "SELECT filename FROM client_record_reads WHERE client_email=? AND error IS NOT NULL "
        "AND attempts >= ?", (email, ATTEMPTS))]
    out["read_count"] = cx.execute(
        "SELECT count(*) FROM client_record_reads WHERE client_email=? AND error IS NULL",
        (email,)).fetchone()[0]
    out["notes"] = [f"{r[0]}: {r[1]}" for r in cx.execute(
        "SELECT filename, note FROM client_record_reads WHERE client_email=? AND "
        "note IS NOT NULL", (email,))]
    return out


def decide(cx, email, finding_id, decision, text=None):
    """Approve (optionally with Glen's corrected wording) or reject one finding."""
    ensure_tables(cx)
    if decision not in ("approve", "reject", "withdraw"):
        return False
    # Withdraw takes an approved finding back out of the summary (review round 1).
    status = {"approve": "approved", "reject": "rejected", "withdraw": "rejected"}[decision]
    text = " ".join((text or "").split())[:200]
    sets, args = ["status=?", "decided_at=?"], [status, _now()]
    if text and decision == "approve":
        sets.append("text=?")
        args.append(text)
    try:
        need = "approved" if decision == "withdraw" else "pending"
        cur = cx.execute(f"UPDATE client_record_findings SET {', '.join(sets)} "
                         "WHERE id=? AND client_email=? AND status=?",
                         (*args, int(finding_id), email, need))
    except Exception:
        # Glen's corrected wording matches a finding already on file: keep that one.
        return False
    cx.commit()
    return cur.rowcount == 1


def approved_diagnoses(cx, email):
    """Approved diagnoses: each becomes a Clinical summary row."""
    ensure_tables(cx)
    return [r[0] for r in cx.execute(
        "SELECT text FROM client_record_findings WHERE client_email=? AND "
        "status='approved' AND kind='diagnosis' ORDER BY id", (email,))]
