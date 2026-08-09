"""Ingest Energy4Life new-client signup notifications from Glen's Gmail inbox."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from dashboard import inbox

SENDER = "noreply@e4l.com"
SUBJECT = "E4L Portal - New Client Sign Up"
GMAIL_QUERY = f'from:({SENDER}) subject:"{SUBJECT}"'
_EMAIL_RE = re.compile(r"(?im)^\s*([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\s*$")


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS e4l_accounts (
        email TEXT PRIMARY KEY,
        client_name TEXT,
        phone TEXT,
        gmail_msg_id TEXT UNIQUE NOT NULL,
        notification_at TEXT,
        ingested_at TEXT NOT NULL
    )""")
    cx.execute("""CREATE TABLE IF NOT EXISTS e4l_account_notification_messages (
        gmail_msg_id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        ingested_at TEXT NOT NULL
    )""")
    cx.commit()


def has_account(cx, email):
    init_table(cx)
    return cx.execute(
        "SELECT 1 FROM e4l_accounts WHERE lower(email)=lower(?) LIMIT 1",
        ((email or "").strip(),),
    ).fetchone() is not None


def parse_notification(text):
    normalized = (text or "").replace("\r", "")
    match = _EMAIL_RE.search(normalized)
    if not match:
        return None
    email = match.group(1).strip().lower()
    before = [line.strip() for line in normalized[:match.start()].splitlines() if line.strip()]
    name = before[-1] if before else ""
    after = [line.strip() for line in normalized[match.end():].splitlines() if line.strip()]
    phone = after[0] if after and "check all your new client" not in after[0].lower() else ""
    return {"email": email, "client_name": name, "phone": phone}


def _header(message, name):
    return inbox._header((message.get("payload") or {}).get("headers") or [], name)


def _notification_at(message):
    try:
        stamp = int(message.get("internalDate") or 0) / 1000
        if stamp:
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        pass
    return _header(message, "Date")


def ingest_notifications(cx, *, service=None, max_messages=500):
    """Scan matching Gmail messages and upsert accounts; safe to rerun."""
    init_table(cx)
    if service is None:
        service = inbox._get_gmail_service()
    listed, token = [], None
    while len(listed) < max_messages:
        kwargs = {"userId": "me", "q": GMAIL_QUERY,
                  "maxResults": min(100, max_messages - len(listed))}
        if token:
            kwargs["pageToken"] = token
        page = service.users().messages().list(**kwargs).execute()
        listed.extend(page.get("messages") or [])
        token = page.get("nextPageToken")
        if not token:
            break

    inserted = updated = skipped = parse_failed = 0
    for ref in listed:
        msg_id = ref.get("id")
        if not msg_id:
            skipped += 1
            continue
        if cx.execute("SELECT 1 FROM e4l_account_notification_messages "
                      "WHERE gmail_msg_id=?", (msg_id,)).fetchone():
            skipped += 1
            continue
        message = service.users().messages().get(
            userId="me", id=msg_id, format="full").execute()
        if (_header(message, "Subject").strip() != SUBJECT
                or SENDER not in _header(message, "From").lower()):
            skipped += 1
            continue
        body = inbox._extract_body(message.get("payload") or {})["plain"]
        parsed = parse_notification(body)
        if not parsed:
            parse_failed += 1
            continue
        was_known = cx.execute(
            "SELECT 1 FROM e4l_accounts WHERE lower(email)=lower(?)", (parsed["email"],)
        ).fetchone() is not None
        now = datetime.now(timezone.utc).isoformat()
        cx.execute("""INSERT INTO e4l_accounts
                      (email,client_name,phone,gmail_msg_id,notification_at,ingested_at)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(email) DO UPDATE SET
                        client_name=excluded.client_name, phone=excluded.phone,
                        gmail_msg_id=excluded.gmail_msg_id,
                        notification_at=excluded.notification_at,
                        ingested_at=excluded.ingested_at""",
                   (parsed["email"], parsed["client_name"], parsed["phone"], msg_id,
                    _notification_at(message), now))
        cx.execute("""INSERT INTO e4l_account_notification_messages
                      (gmail_msg_id,email,ingested_at) VALUES (?,?,?)
                      ON CONFLICT(gmail_msg_id) DO NOTHING""",
                   (msg_id, parsed["email"], now))
        updated += int(was_known)
        inserted += int(not was_known)
    cx.commit()
    return {"listed": len(listed), "inserted": inserted, "updated": updated,
            "skipped": skipped, "parse_failed": parse_failed}
