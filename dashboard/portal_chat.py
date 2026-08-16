"""Persistent client-portal chat thread, keyed by client email.

Before this, the portal "Ask Dr. Glen" chat lived only in the browser (lost on
refresh) — only a rolled-up ASH ally summary was stored. This keeps the actual
turns so the thread is remembered across sessions and Dr. Glen can reply into it
from the console. Pure sqlite; caller passes the connection.
"""
import sqlite3
from datetime import datetime, timezone

from dashboard import dbwrite

# role values
CLIENT = "client"          # the portal client
ASSISTANT = "assistant"    # the AI concierge ("Ask Dr. Glen")
PRACTITIONER = "practitioner"  # a real reply from Dr. Glen / Rae


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_chat_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, role TEXT NOT NULL,
        author TEXT, content TEXT NOT NULL, created_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_pcm_email ON portal_chat_messages(email, id)")
    cx.commit()


def init_email_import_table(cx):
    """Durable Gmail-message ledger for retry-safe email imports."""
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_email_imports(
        source_message_id TEXT PRIMARY KEY, portal_message_id INTEGER NOT NULL,
        email TEXT NOT NULL, imported_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_pei_email ON portal_email_imports(email)")
    cx.commit()


def add_message(cx, email, role, content, author=None):
    """Append a message to a client's thread. role = client|assistant|practitioner.
    Returns the new row id, or None if email/content is empty (no-op)."""
    init_table(cx)
    e = (email or "").strip().lower()
    c = (content or "").strip()
    if not e or not c:
        return None
    new_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO portal_chat_messages(email,role,author,content,created_at) VALUES(?,?,?,?,?)",
        (e, role, (author or "").strip() or None, c, _now()))
    cx.commit()
    return new_id


def add_message_once(cx, email, role, content, author=None):
    """Append an exact message only when it is not already in the thread.

    Returns ``(id, created)``. Existing rows return their id with ``False``;
    this gives console-driven email imports retry-safe semantics without adding
    transport metadata to the client-visible chat schema.
    """
    init_table(cx)
    e = (email or "").strip().lower()
    c = (content or "").strip()
    if not e or not c:
        return None, False
    row = cx.execute(
        "SELECT id FROM portal_chat_messages "
        "WHERE email=? AND role=? AND content=? ORDER BY id LIMIT 1",
        (e, role, c)).fetchone()
    if row:
        existing_id = row["id"] if hasattr(row, "keys") else row[0]
        return int(existing_id), False
    return add_message(cx, e, role, c, author=author), True


def import_email_once(cx, email, content, source_message_id, author=None):
    """Import one Gmail message as client-authored context exactly once.

    ``source_message_id`` is Gmail's immutable message id. Exact-content
    deduplication remains as a compatibility fallback for imports made before
    the ledger existed.
    """
    init_email_import_table(cx)
    source_id = (source_message_id or "").strip()
    if not source_id:
        return None, False
    row = cx.execute(
        "SELECT portal_message_id FROM portal_email_imports "
        "WHERE source_message_id=?", (source_id,)).fetchone()
    if row:
        existing_id = row["portal_message_id"] if hasattr(row, "keys") else row[0]
        return int(existing_id), False

    message_id, created = add_message_once(
        cx, email, CLIENT, content, author=author)
    if message_id is None:
        return None, False
    cx.execute(
        "INSERT INTO portal_email_imports"
        "(source_message_id,portal_message_id,email,imported_at) VALUES(?,?,?,?)",
        (source_id, int(message_id), (email or "").strip().lower(), _now()))
    cx.commit()
    return int(message_id), bool(created)


def list_messages(cx, email, limit=100):
    """The client's thread in chronological order (oldest first), capped at `limit`
    most-recent messages."""
    init_table(cx)
    cx.row_factory = sqlite3.Row
    e = (email or "").strip().lower()
    rows = cx.execute(
        "SELECT role, author, content, created_at FROM portal_chat_messages "
        "WHERE email=? ORDER BY id DESC LIMIT ?", (e, int(limit or 100))).fetchall()
    return [{"role": r["role"], "author": r["author"] or "", "content": r["content"],
             "created_at": r["created_at"]} for r in reversed(rows)]


def record_exchange(cx, email, query, answer, client_name=None):
    """Persist one concierge turn: the client's message + the AI answer.
    Returns the client message's row id (or None if it was a no-op), so a
    caller can attribute a downstream extraction (e.g. health_suggestions)
    back to the turn that produced it."""
    client_msg_id = add_message(cx, email, CLIENT, query, author=client_name or "You")
    add_message(cx, email, ASSISTANT, answer, author="Ask Dr. Glen")
    return client_msg_id
