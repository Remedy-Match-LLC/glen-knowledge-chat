"""Email suppression list: addresses we must stop emailing — permanent delivery
failures (hard bounces) and recipient opt-outs, told apart by bounce_type. Populated by the local bounce scanner via the
email_suppression.add console action. Spam-blocks are NOT stored here (the address
is valid — our sender reputation is the problem). Reversible: delete a row if an
address recovers.

is_suppressed is the one check every app sender calls. It reads TWO stores:
  (a) this table, any bounce_type — an address-level block, and
  (b) the People hub's `consent:unsubscribed` person tag — the person asked to stop.
A block in either store is a block. Until 2026-09-15 only (a) was read, so an
opt-out recorded only in the hub was invisible to every sender."""
import json
import sqlite3

from dashboard import db

# The People hub's refusal tag. ONE meaning: the person asked to stop email.
# Matched as a whole tag, never as a substring: `consent:sms-unsubscribed` is a
# text opt-out and must not gate email.
UNSUBSCRIBED_TAG = "consent:unsubscribed"


def init_table(cx, *, commit=True):
    cx.execute("""CREATE TABLE IF NOT EXISTS email_suppression (
        email TEXT PRIMARY KEY, bounce_type TEXT, reason TEXT,
        source TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    if commit:
        cx.commit()


def _table_reason(cx, email):
    """The row's bounce_type when the address has a row, else None."""
    try:
        r = cx.execute("SELECT bounce_type FROM email_suppression WHERE email=lower(?)",
                       (email,)).fetchone()
    except db.OperationalError:
        return None
    if not r:
        return None
    return str(r[0] or "").strip() or "suppressed"


def _tags_of(raw):
    """people.tags is a JSON array stored as TEXT. Tolerate a list already decoded."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def has_unsubscribed_tag(tags):
    """True when the tag list carries the exact refusal tag (whole tag, any case)."""
    return any(isinstance(t, str) and t.strip().lower() == UNSUBSCRIBED_TAG
               for t in tags or [])


def _hub_unsubscribed(cx, email):
    try:
        r = cx.execute("SELECT tags FROM people WHERE email=?", (email,)).fetchone()
    except db.OperationalError:
        return False  # no people table on this connection (a test db, a side db)
    if not r:
        return False
    return has_unsubscribed_tag(_tags_of(r[0]))


def normalize(email):
    """Trim and lowercase. '' for a blank or a non-string."""
    return email.strip().lower() if isinstance(email, str) else ""


def suppression_reason(cx, email):
    """Why this address must not be emailed, or None when it may be.

    The table's bounce_type (hard, ghl-dnd, optout, ...) when the address has a row;
    otherwise "consent:unsubscribed" when the hub person carries that exact tag.
    This is the one implementation. is_suppressed and the console check both call it."""
    email = normalize(email)
    if not email:
        return None
    reason = _table_reason(cx, email)
    if reason is not None:
        return reason
    if _hub_unsubscribed(cx, email):
        return UNSUBSCRIBED_TAG
    return None


def is_suppressed(cx, email):
    return suppression_reason(cx, email) is not None


def add(cx, email, bounce_type, reason, source, *, overwrite=True, commit=True):
    """Record an address-level block.

    overwrite=False keeps an existing row exactly as it is (ON CONFLICT DO NOTHING).
    The People hub upsert uses that, so an hourly GHL sync never rewrites a bounce
    scanner's row or downgrades an opt-out. commit=False lets a caller that holds the
    transaction (the hub upsert) keep holding it."""
    if not email:
        return
    if overwrite:
        conflict = ("DO UPDATE SET bounce_type=excluded.bounce_type, "
                    "reason=excluded.reason, source=excluded.source")
    else:
        conflict = "DO NOTHING"
    cx.execute(f"""INSERT INTO email_suppression(email,bounce_type,reason,source)
        VALUES(lower(?),?,?,?) ON CONFLICT(email) {conflict}""",
               (email.strip().lower(), bounce_type, reason, source))
    if commit:
        cx.commit()


def list_recent(cx, limit=200):
    cx.row_factory = sqlite3.Row
    return [dict(r) for r in cx.execute(
        "SELECT * FROM email_suppression ORDER BY created_at DESC LIMIT ?", (limit,))]


def add_optout(cx, email, source):
    """Record a recipient-initiated opt-out. Distinct from a bounce: the address is
    valid, the person asked us to stop. Stored here so every sender that already
    calls is_suppressed honors it with no further change. Never downgrades an
    existing hard bounce — a dead address stays dead."""
    if not email:
        return
    cx.execute("""INSERT INTO email_suppression(email,bounce_type,reason,source)
        VALUES(lower(?),'optout','recipient unsubscribed',?)
        ON CONFLICT(email) DO UPDATE SET source=excluded.source""",
        (email.strip().lower(), source))
    cx.commit()
