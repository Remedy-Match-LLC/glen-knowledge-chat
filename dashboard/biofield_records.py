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
from datetime import datetime, timezone

KINDS = ("diagnosis", "medication", "result", "procedure")
KIND_LABEL = {"diagnosis": "Diagnosis", "medication": "Medication",
              "result": "Test result", "procedure": "Procedure"}
PAGES_MAX = 30          # pages read per record
OCR_MIN_CHARS = 40      # a PDF page with less text than this is treated as a scan
ASK_MAX = 60_000        # characters of one record sent to the model
BUDGET = 240            # seconds per read request; the rest is read on the next one
FILE_MAX = 10_000_000   # bytes

_IMAGE = ("image/jpeg", "image/png", "image/tiff", "image/gif", "image/bmp", "image/webp")
_TEXTUTIL = {".rtf": "rtf", ".doc": "doc", ".docx": "docx", ".odt": "odt"}


def ensure_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS client_record_findings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        client_email  TEXT NOT NULL,
        message_id    TEXT NOT NULL,
        filename      TEXT NOT NULL,
        page          INTEGER,
        kind          TEXT NOT NULL,
        text          TEXT NOT NULL,
        finding_date  TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        created_at    TEXT NOT NULL,
        decided_at    TEXT,
        UNIQUE (client_email, message_id, filename, page, kind, text))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS client_record_reads (
        client_email TEXT NOT NULL,
        message_id   TEXT NOT NULL,
        filename     TEXT NOT NULL,
        read_at      TEXT NOT NULL,
        pages        INTEGER,
        error        TEXT,
        PRIMARY KEY (client_email, message_id, filename))""")


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


def _run(cmd, data, timeout=60):
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


def extract_pages(mime, filename, data, *, ocr=ocr_image, rasterise=pdf_page_png):
    """One text per page. Raises when the record cannot be read at all."""
    kind = record_kind(mime, filename)
    if kind == "pdf":
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages[:PAGES_MAX], 1):
            text = (page.extract_text() or "").strip()
            if len(text) < OCR_MIN_CHARS:          # a scanned page
                text = ocr(rasterise(data, i)).strip()
            pages.append(text)
        return pages
    if kind == "image":
        return [ocr(data).strip()]
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


def ask(complete, pages, filename):
    """The model's findings for one record, validated."""
    body, used = [], 0
    for i, text in enumerate(pages, 1):
        chunk = f"[page {i}]\n{text}\n"
        if used + len(chunk) > ASK_MAX:
            break
        body.append(chunk)
        used += len(chunk)
    if not used or not any(p.strip() for p in pages):
        return []
    raw = complete(SYSTEM, f"Record: {filename}\n\n" + "".join(body))
    try:
        found = json.loads(raw).get("findings") or []
    except (ValueError, AttributeError):
        raise ValueError("the model's reply was not JSON")
    out = []
    for f in found if isinstance(found, list) else []:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "").strip().lower()
        text = " ".join(str(f.get("text") or "").split())
        if kind not in KINDS or not (3 <= len(text) <= 200):
            continue
        try:
            page = int(f.get("page"))
            page = page if 1 <= page <= len(pages) else None
        except (TypeError, ValueError):
            page = None
        date = str(f.get("date") or "").strip()
        date = date if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", date) else ""
        out.append({"kind": kind, "text": text, "page": page, "date": date})
    return out


def unread(cx, email):
    """Records in this client's kept mail that have not been read here yet."""
    ensure_tables(cx)
    done = {(r[0], r[1]) for r in cx.execute(
        "SELECT message_id, filename FROM client_record_reads WHERE client_email=?", (email,))}
    try:
        rows = cx.execute("SELECT message_id, attachments FROM client_gmail_messages "
                          "WHERE client_email=? AND kept=1 ORDER BY sent_at DESC",
                          (email,)).fetchall()
    except Exception:
        return []
    out = []
    for mid, atts in rows:
        for a in json.loads(atts or "[]"):
            name = a.get("filename") or ""
            if name and record_kind(a.get("mime"), name) and (mid, name) not in done:
                out.append({"message_id": mid, "filename": name, "mime": a.get("mime")})
    return out


def _attachment_bytes(service, message_id, filename):
    """Fetch one named attachment again. Gmail attachment ids are not stable, so the
    part is found by its filename in a fresh copy of the message."""
    from dashboard.biofield_gmail import _b64, _walk
    users = service.users()
    msg = users.messages().get(userId="me", id=message_id, format="full").execute()
    for p in _walk(msg.get("payload") or {}):
        if (p.get("filename") or "") == filename:
            body = p.get("body") or {}
            if int(body.get("size") or 0) > FILE_MAX:
                raise ValueError("over 10 MB")
            if body.get("data"):
                return _b64(body["data"])
            got = users.messages().attachments().get(
                userId="me", messageId=message_id, id=body.get("attachmentId")).execute()
            return _b64(got.get("data"))
    raise LookupError("attachment not found")


def read_records(cx, email, service, complete, *, budget=BUDGET, clock=None,
                 extract=extract_pages):
    """Read each unread record, store its findings as pending. Returns counts.
    A record that cannot be read is recorded with its error, so it is not read again
    on every page load. A record Gmail would not hand over is tried again next time."""
    import time
    clock = clock or time.monotonic
    deadline = clock() + budget
    ensure_tables(cx)
    done = failed = added = 0
    todo = unread(cx, email)
    for rec in todo:
        if clock() > deadline:
            break
        mid, name = rec["message_id"], rec["filename"]
        try:
            data = _attachment_bytes(service, mid, name)
        except Exception:
            failed += 1      # Gmail, not the record: tried again next time
            continue
        try:
            pages = extract(rec["mime"], name, data)
            findings = ask(complete, pages, name)
            error = None
        except Exception as e:
            pages, findings, error = [], [], type(e).__name__
            failed += 1
        now = _now()
        for f in findings:
            cur = cx.execute(
                "INSERT OR IGNORE INTO client_record_findings(client_email, message_id, "
                "filename, page, kind, text, finding_date, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (email, mid, name, f["page"], f["kind"], f["text"], f["date"], now))
            added += max(0, cur.rowcount or 0)
        cx.execute("INSERT OR REPLACE INTO client_record_reads(client_email, message_id, "
                   "filename, read_at, pages, error) VALUES(?,?,?,?,?,?)",
                   (email, mid, name, now, len(pages), error))
        cx.commit()
        done += 1
    return {"read": done, "failed": failed, "added": added,
            "remaining": len(unread(cx, email))}


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
        out[status].append({"id": fid, "kind": kind, "kind_label": KIND_LABEL[kind],
                            "text": text, "date": date or "", "filename": name,
                            "page": page, "email_date": sent[:10], "subject": subject})
    out["failed"] = [r[0] for r in cx.execute(
        "SELECT filename FROM client_record_reads WHERE client_email=? AND error IS NOT NULL",
        (email,))]
    return out


def decide(cx, email, finding_id, decision, text=None):
    """Approve (optionally with Glen's corrected wording) or reject one finding."""
    ensure_tables(cx)
    if decision not in ("approve", "reject"):
        return False
    status = "approved" if decision == "approve" else "rejected"
    text = " ".join((text or "").split())[:200]
    sets, args = ["status=?", "decided_at=?"], [status, _now()]
    if text and decision == "approve":
        sets.append("text=?")
        args.append(text)
    try:
        cur = cx.execute(f"UPDATE client_record_findings SET {', '.join(sets)} "
                         "WHERE id=? AND client_email=?", (*args, int(finding_id), email))
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
