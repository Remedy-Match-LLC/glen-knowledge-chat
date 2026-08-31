"""Email suppression list: addresses we must stop emailing — permanent delivery
failures (hard bounces) and recipient opt-outs, told apart by bounce_type. Populated by the local bounce scanner via the
email_suppression.add console action. Spam-blocks are NOT stored here (the address
is valid — our sender reputation is the problem). Reversible: delete a row if an
address recovers."""
import sqlite3
from dashboard import db


def init_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS email_suppression (
        email TEXT PRIMARY KEY, bounce_type TEXT, reason TEXT,
        source TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    cx.commit()


def is_suppressed(cx, email):
    if not email:
        return False
    try:
        r = cx.execute("SELECT 1 FROM email_suppression WHERE email=lower(?)",
                       (email.strip().lower(),)).fetchone()
    except db.OperationalError:
        return False
    return bool(r)


def add(cx, email, bounce_type, reason, source):
    if not email:
        return
    cx.execute("""INSERT INTO email_suppression(email,bounce_type,reason,source)
        VALUES(lower(?),?,?,?) ON CONFLICT(email) DO UPDATE SET
        bounce_type=excluded.bounce_type, reason=excluded.reason,
        source=excluded.source""", (email.strip().lower(), bounce_type, reason, source))
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
