"""Photo review list: client photos that arrived by email or portal chat.

Glen, 2026-11-23: a photo a client emails us, or sends in the portal chat, waits here.
It reaches the client's record only when staff click "Use as photo". Nothing in this
module writes to client_photos on its own.

The email scan is READ ONLY on Gmail. It calls list and get, never modify, never a
label change, never mark read. Unread is the reply queue (inbox-email-policy).
"""
import base64
import datetime
import re

SOURCES = ("email", "portal-chat")
STATUSES = ("pending", "used", "dismissed")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_BYTES = 5 * 1024 * 1024
# A signature logo or a tracking pixel is small. A face photo is not.
MIN_BYTES = 15 * 1024
_TYPE_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
_EXT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp"}


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _norm(email):
    return (email or "").strip().lower()


def signature_ok(ctype, blob):
    """The declared type matches the file's own first bytes."""
    if ctype == "image/jpeg":
        return blob[:3] == b"\xff\xd8\xff"
    if ctype == "image/png":
        return blob[:8] == b"\x89PNG\r\n\x1a\n"
    if ctype == "image/webp":
        return blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    return False


def init_table(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS photo_inbox("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, "
        "source TEXT NOT NULL, source_ref TEXT NOT NULL, filename TEXT, mime TEXT, "
        "image_blob BYTEA, byte_size INTEGER, received_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'pending', decided_by_role TEXT, decided_at TEXT, "
        "created_at TEXT)")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_photo_inbox_src "
               "ON photo_inbox(source, source_ref)")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_photo_inbox_status ON photo_inbox(status)")


def enqueue(cx, email, source, source_ref, blob, mime, filename="", received_at=None):
    """Add one item. Returns True when a new row was written, False when it was
    already there (same source and source_ref) or refused."""
    e = _norm(email)
    ref = str(source_ref or "").strip()
    mime = _TYPE_ALIASES.get((mime or "").lower(), (mime or "").lower())
    if not e or source not in SOURCES or not ref or not blob:
        return False
    if mime not in IMAGE_TYPES or len(blob) > MAX_BYTES or not signature_ok(mime, blob):
        return False
    init_table(cx)
    cur = cx.execute(
        "INSERT INTO photo_inbox(email, source, source_ref, filename, mime, image_blob, "
        "byte_size, received_at, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source, source_ref) DO NOTHING",
        (e, source, ref, (filename or "")[:200], mime, blob, len(blob),
         received_at or _now(), "pending", _now()))
    cx.commit()
    return (cur.rowcount or 0) > 0


_COLS = ("id", "email", "source", "source_ref", "filename", "mime", "byte_size",
         "received_at", "status", "decided_by_role", "decided_at")


def list_items(cx, status="pending", limit=200):
    init_table(cx)
    rows = cx.execute(
        "SELECT " + ", ".join(_COLS) + " FROM photo_inbox WHERE status=? "
        "ORDER BY received_at DESC, id DESC LIMIT ?", (status, int(limit))).fetchall()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def get(cx, item_id, with_blob=False):
    init_table(cx)
    cols = _COLS + (("image_blob",) if with_blob else ())
    r = cx.execute("SELECT " + ", ".join(cols) + " FROM photo_inbox WHERE id=?",
                   (int(item_id),)).fetchone()
    if not r:
        return None
    d = dict(zip(cols, tuple(r)))
    if with_blob and d.get("image_blob") is not None:
        d["image_blob"] = bytes(d["image_blob"])
    return d


def decide(cx, item_id, status, role):
    """Mark a pending item used or dismissed. Returns False when it was not pending."""
    if status not in ("used", "dismissed"):
        raise ValueError("status must be used or dismissed")
    init_table(cx)
    cur = cx.execute(
        "UPDATE photo_inbox SET status=?, decided_by_role=?, decided_at=? "
        "WHERE id=? AND status='pending'", (status, role or "", _now(), int(item_id)))
    cx.commit()
    return (cur.rowcount or 0) > 0


# ── Email scan ──────────────────────────────────────────────────────────────

_ADDR = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def sender_address(from_header):
    m = re.search(r"<([^>]+)>", from_header or "")
    raw = m.group(1) if m else (from_header or "")
    hit = _ADDR.search(raw)
    return _norm(hit.group(0)) if hit else ""


def _image_type(part):
    mime = _TYPE_ALIASES.get((part.get("mimeType") or "").lower(),
                             (part.get("mimeType") or "").lower())
    if mime in IMAGE_TYPES:
        return mime
    if mime in ("application/octet-stream", ""):
        name = (part.get("filename") or "").lower()
        for ext, t in _EXT_TYPES.items():
            if name.endswith(ext):
                return t
    return ""


def _image_parts(payload):
    out = []

    def walk(p, path):
        body = p.get("body") or {}
        if (p.get("filename") or "").strip() and body.get("attachmentId"):
            mime = _image_type(p)
            if mime:
                out.append((p.get("partId") or path, p, mime))
        for i, child in enumerate(p.get("parts") or []):
            walk(child, f"{path}.{i}")

    walk(payload or {}, "0")
    return out


def scan_gmail(cx, svc, is_known_client, days=14, max_messages=200):
    """Enqueue image attachments from known clients in the last `days` days.

    `svc` is a Gmail API service. Only users().messages().list, .get and
    .attachments().get are called. `is_known_client(email)` returns the email to file
    under, or a false value to skip the sender.

    The attachment id Gmail returns changes between fetches, so an item is keyed by
    message id plus part id, which do not."""
    init_table(cx)
    counts = {"messages": 0, "images": 0, "added": 0, "already": 0,
              "unknown_sender": 0, "skipped": 0}
    msgs_api = svc.users().messages()
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=int(days))).strftime("%Y/%m/%d")
    q = f"has:attachment after:{since}"
    ids, page = [], None
    while len(ids) < max_messages:
        kw = {"userId": "me", "q": q, "maxResults": min(100, max_messages - len(ids))}
        if page:
            kw["pageToken"] = page
        res = msgs_api.list(**kw).execute() or {}
        ids.extend(m["id"] for m in (res.get("messages") or []) if m.get("id"))
        page = res.get("nextPageToken")
        if not page:
            break
    for mid in ids:
        msg = msgs_api.get(userId="me", id=mid, format="full").execute() or {}
        counts["messages"] += 1
        payload = msg.get("payload") or {}
        parts = _image_parts(payload)
        if not parts:
            continue
        headers = {(h.get("name") or "").lower(): h.get("value") or ""
                   for h in (payload.get("headers") or [])}
        sender = sender_address(headers.get("from", ""))
        email = is_known_client(sender) if sender else None
        if not email:
            counts["unknown_sender"] += len(parts)
            continue
        try:
            received = datetime.datetime.utcfromtimestamp(
                int(msg.get("internalDate") or 0) / 1000).isoformat() + "Z"
        except (TypeError, ValueError):
            received = _now()
        for part_id, part, mime in parts:
            counts["images"] += 1
            ref = f"{mid}:{part_id}"
            if cx.execute("SELECT 1 FROM photo_inbox WHERE source='email' AND source_ref=?",
                          (ref,)).fetchone():
                counts["already"] += 1
                continue
            size = int((part.get("body") or {}).get("size") or 0)
            if size and (size < MIN_BYTES or size > MAX_BYTES):
                counts["skipped"] += 1
                continue
            att = msgs_api.attachments().get(
                userId="me", messageId=mid, id=part["body"]["attachmentId"]).execute() or {}
            raw = att.get("data") or ""
            blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)) if raw else b""
            if len(blob) < MIN_BYTES:
                counts["skipped"] += 1
                continue
            if enqueue(cx, email, "email", ref, blob, mime,
                       filename=part.get("filename") or "", received_at=received):
                counts["added"] += 1
            else:
                counts["skipped"] += 1
    return counts
