"""A client's own emails and attachments, read into the local Biofield Intake (Glen,
2026-10-16).

Josie Gagnon's panels came back empty although she had written in. Glen: "Let's create
the automations." His choices: read the drglen mailbox (support@ forwards into it),
match on the sender address, keep everything on this Mac, and mine it with no review.

Read only. The token carries gmail.readonly, nothing here calls a modify endpoint, and the
refreshed token is never written back, so a shared token cannot be narrowed. Nothing is
ever marked read.

An address that E4L shows for more than one person is held: whose email is it? Nothing
from it is fetched or mined, and the page says so.
"""
import base64
import email.utils
import io
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

DAYS = 365          # how far back a client's mail is read
LIMIT = 25          # newest messages per client
TEXT_MAX = 20_000   # characters kept per message body or attachment
FETCH_MAX = 10_000_000   # bytes: a larger attachment is named, never downloaded
TIMEOUT = 20        # seconds per Gmail call: header save waits on mining
BUDGET = 30         # seconds for one client's fetch; the rest waits for the next run
MINE_MAX = 40_000   # characters handed to mining for one client

TOKEN = os.path.expanduser("~/.config/google/token.json")
MAILBOX = "drglenswartwout@gmail.com"   # Glen's choice; support@ forwards into it

_EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}")


_COLUMNS = {"message_id", "client_email", "sent_at", "subject", "body", "attachments",
            "kept", "fetched_at"}


def ensure_table(cx):
    # A cache of Gmail, so a table of an older shape is rebuilt, never patched
    # (review round 3): the mail is simply read again.
    cols = {r[1] for r in cx.execute("PRAGMA table_info(client_gmail_messages)")}
    if cols and cols != _COLUMNS:
        cx.execute("DROP TABLE client_gmail_messages")
    cx.execute("""CREATE TABLE IF NOT EXISTS client_gmail_messages (
        message_id   TEXT NOT NULL,
        client_email TEXT NOT NULL,
        sent_at      TEXT,
        subject      TEXT,
        body         TEXT,
        attachments  TEXT,
        kept         INTEGER NOT NULL DEFAULT 1,
        fetched_at   TEXT NOT NULL,
        PRIMARY KEY (client_email, message_id))""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_client_gmail_messages_email "
               "ON client_gmail_messages(client_email)")


def clean_email(email):
    """The address, lower-cased, or "" when it is not a plain address. It goes into a
    Gmail search, so nothing else may ride along with it."""
    e = (email or "").strip().lower()
    return e if _EMAIL.fullmatch(e) else ""


def _name_key(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z]+", " ", s).split())


def people_on_address(e4l_db, email):
    """How many differently named E4L records carry this address (people and pets), or
    None when e4l.db cannot be read. A failed check is never taken as "one person"
    (review round 2). One person's duplicate E4L accounts count once; a name with no
    Latin letters is compared as written."""
    try:
        with sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True) as cx:
            rows = cx.execute("SELECT name FROM e4l_clients WHERE lower(trim(email))=?",
                              (email,)).fetchall()
    except sqlite3.Error:
        return None
    keys = {_name_key(r[0]) or (r[0] or "").strip().lower() for r in rows}
    return len({k for k in keys if k})


_FORWARD = re.compile(r"^(-{5,}\s*Forwarded message\s*-{5,}|Begin forwarded message:|_{10,})$")


def strip_quoted(text):
    """The client's own words. Quoted ">" lines and the "On ... wrote:" line go, but text
    after it stays: a client may answer inline (review round 1). An Outlook-style reply
    quotes with no marks, so its "From:" block ends her words."""
    lines = (text or "").splitlines()
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("-----Original Message") or _FORWARD.match(s):
            break     # what follows is someone else's message (review round 3)
        if (re.match(r"^From:\s", s)
                and any(re.match(r"^(Sent|Date):\s", x.strip()) for x in lines[i + 1:i + 3])):
            break
        if re.match(r"^On .{4,200} wrote:$", s) or s.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _b64(data):
    return base64.urlsafe_b64decode((data or "") + "=" * (-len(data or "") % 4))


def _html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    # A reply's quoted history: the earlier message is not the client's words.
    html = re.sub(r"(?is)<blockquote.*?</blockquote>", " ", html)
    html = re.sub(r'(?is)<div[^>]*class="?[^">]*(gmail_quote|yahoo_quoted|divRplyFwdMsg)'
                  r'.*$', " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"[ \t]+", " ", text)


def _readable(mime, filename):
    mime, name = (mime or "").lower(), (filename or "").lower()
    if "rtf" in mime or name.endswith(".rtf"):
        return False      # raw RTF is markup, not words (found on a live mailbox)
    return (mime == "application/pdf" or name.endswith((".pdf", ".txt", ".csv"))
            or mime.startswith("text/"))


def attachment_text(mime, filename, data):
    """Readable text from one attachment, or "" for a kind that cannot be read here
    (images, Word files). The file itself is never kept."""
    mime = (mime or "").lower()
    name = (filename or "").lower()
    if not _readable(mime, filename):
        return ""
    try:
        if mime == "application/pdf" or name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:30])
        elif mime.startswith("text/") or name.endswith((".txt", ".csv")):
            text = data.decode("utf-8", "replace")
            if mime == "text/html":
                text = _html_to_text(text)
        else:
            return ""
    except ImportError:
        raise             # pypdf missing: the message is retried once it is installed
    except Exception:
        return None       # it should have been readable: the caller says so
    return text.strip()[:TEXT_MAX]


def _walk(part):
    yield part
    if (part.get("mimeType") or "").lower().startswith("message/"):
        return            # a forwarded message is someone else's words (review round 3)
    for p in part.get("parts") or []:
        yield from _walk(p)


def parse_message(msg, get_attachment):
    """(subject, sent_at, body, [attachment dicts]) from a Gmail format=full message.
    get_attachment(attachment_id) -> bytes."""
    payload = msg.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
    plain, html, atts = [], [], []

    def _data(body):
        if body.get("data"):
            return _b64(body["data"])
        # Gmail hands a large body, or any attachment, by id (review round 2).
        return get_attachment(body["attachmentId"]) if body.get("attachmentId") else b""
    for p in _walk(payload):
        mime = (p.get("mimeType") or "").lower()
        body = p.get("body") or {}
        fname = p.get("filename") or ""
        if mime.startswith("message/"):
            atts.append({"filename": (fname or "forwarded message")[:200], "mime": mime,
                         "text": "", "unreadable": False})
            continue
        if fname:
            # A photo, video or logo is named, never downloaded (review round 1).
            if not _readable(mime, fname) or int(body.get("size") or 0) > FETCH_MAX:
                atts.append({"filename": fname[:200], "mime": mime, "text": "",
                             "unreadable": False})
                continue
            text = attachment_text(mime, fname, _data(body) or b"")
            atts.append({"filename": fname[:200], "mime": mime, "text": text or "",
                         "unreadable": text is None})
        elif mime == "text/plain":
            plain.append((_data(body) or b"").decode("utf-8", "replace"))
        elif mime == "text/html":
            html.append(_html_to_text((_data(body) or b"").decode("utf-8", "replace")))
    body = "\n".join(plain) if any(x.strip() for x in plain) else "\n".join(html)
    try:
        sent = datetime.fromtimestamp(int(msg.get("internalDate")) / 1000,
                                      timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        sent = ""
    sender = email.utils.parseaddr(headers.get("from", ""))[1].strip().lower()
    return headers.get("subject", "")[:300], sent, strip_quoted(body)[:TEXT_MAX], atts, sender


def default_service():
    """A read-only Gmail client from the local token. The token is refreshed in memory
    only: writing it back could narrow the scopes every other job relies on."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN)
    if not creds.valid:
        req = Request()
        creds.refresh(lambda *a, **k: req(*a, **{**k, "timeout": TIMEOUT}))
    import google_auth_httplib2
    import httplib2
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=TIMEOUT))
    svc = build("gmail", "v1", http=http, cache_discovery=False)
    # The token could be re-authorised for another account: read only Glen's.
    who = (svc.users().getProfile(userId="me").execute().get("emailAddress") or "").lower()
    if who != MAILBOX:
        raise PermissionError("the Gmail token is not for the drglen mailbox")
    return svc


def ingest(cx, email, service, *, days=DAYS, limit=LIMIT, budget=BUDGET, clock=None):
    """Store this client's newest messages that are not stored yet. Returns how many
    were listed, how many are new, and how many could not be read this time (they are
    tried again next run). Raises when the search itself fails."""
    ensure_table(cx)
    users = service.users()
    listed = users.messages().list(userId="me", q=f"from:{email} newer_than:{days}d",
                                   maxResults=limit).execute().get("messages") or []
    have = {r[0] for r in cx.execute(
        "SELECT message_id FROM client_gmail_messages WHERE client_email=?", (email,))}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    import time
    clock = clock or time.monotonic
    deadline = clock() + budget
    new = failed = 0
    for m in listed:
        mid = m.get("id")
        if not mid or mid in have:
            continue
        if clock() > deadline:
            failed += 1   # left for the next run
            continue

        def _att(aid, _mid=mid):
            got = users.messages().attachments().get(userId="me", messageId=_mid,
                                                     id=aid).execute()
            return _b64(got.get("data"))
        try:   # one bad message must not stop the rest (review round 1)
            full = users.messages().get(userId="me", id=mid, format="full").execute()
            subject, sent, body, atts, sender = parse_message(full, _att)
        except Exception:
            failed += 1
            continue
        # Gmail's from: is looser than one address; keep only an exact sender. Any
        # other is remembered as not kept, so it is not fetched again.
        kept = int(sender == email)
        cur = cx.execute("INSERT OR IGNORE INTO client_gmail_messages(message_id, "
                         "client_email, sent_at, subject, body, attachments, kept, fetched_at) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (mid, email, sent, subject if kept else "", body if kept else "",
                          json.dumps(atts) if kept else "[]", kept, now))
        cx.commit()       # one message at a time: never hold the write lock (round 3)
        new += kept * max(0, cur.rowcount or 0)
    return {"listed": len(listed), "new": new, "failed": failed}


def stored_text(cx, email, *, days=DAYS, limit=LIMIT, max_chars=MINE_MAX):
    """The stored messages as one text for mining, newest first."""
    ensure_table(cx)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    # The window applies to what is mined, not only to what is fetched (review round 2).
    rows = cx.execute("SELECT sent_at, subject, body, attachments FROM client_gmail_messages "
                      "WHERE client_email=? AND kept=1 AND sent_at >= ? ORDER BY sent_at DESC LIMIT ?",
                      (email, since, limit)).fetchall()
    parts, unreadable = [], 0
    for sent, subject, body, atts in rows:
        chunk = [f"email {(sent or '')[:10]}: {subject or ''}".strip(), body or ""]
        for a in json.loads(atts or "[]"):
            unreadable += bool(a.get("unreadable"))
            if a.get("text"):
                chunk.append(f"attachment {a.get('filename', '')}:\n{a['text']}")
        parts.append("\n".join(c for c in chunk if c))
    return "\n\n".join(parts)[:max_chars], unreadable


def client_email_text(cx, email, e4l_db, service_factory=default_service):
    """{"text", "held", "error"} for one client. Never raises.

    held: the address belongs to more than one E4L record, so nothing was read.
    error: Gmail could not be reached; anything stored earlier is still returned."""
    email = clean_email(email)
    if not email:
        return {"text": "", "held": 0, "error": None}
    n = people_on_address(e4l_db, email)
    if n is None:
        return {"text": "", "held": 0, "error": "Email was not read: E4L could not be "
                                                 "checked for a shared address."}
    if n > 1:
        return {"text": "", "held": n, "error": None}
    error = None
    try:
        got = ingest(cx, email, service_factory())
        if got.get("failed"):
            error = f"{got['failed']} email(s) could not be read; they are retried next time."
    except Exception as e:
        error = f"Gmail could not be read ({type(e).__name__})."
    try:
        text, unreadable = stored_text(cx, email)
        if unreadable:
            note = f"{unreadable} attachment(s) could not be read."
            error = f"{error} {note}" if error else note
        return {"text": text, "held": 0, "error": error}
    except Exception as e:
        return {"text": "", "held": 0, "error": f"Stored email could not be read ({type(e).__name__})."}
