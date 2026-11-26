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
        r = cx.execute("SELECT tags FROM people WHERE lower(email)=?", (email,)).fetchone()
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


# Addresses per IN (...) query. Postgres takes far more; this keeps one statement small.
_BATCH_IN = 500


def suppression_reasons(cx, emails):
    """suppression_reason for many addresses at once: {normalised address: reason or None}.

    The console check called suppression_reason per address, two queries each, about
    0.22 s an address on prod (2026-11-26), so 505 addresses ran past a 90 s client
    timeout. This reads both stores in two IN queries per 500 addresses.

    It gives suppression_reason's answer for every address, with two differences, both
    stricter. An address held by more than one People row is blocked if ANY row carries
    the tag, where suppression_reason reads whichever row comes first. And a database
    error raises, where suppression_reason reads it as "no row"; the console route turns
    that into a 500, so the caller fails closed (review, 2026-11-26)."""
    wanted = sorted({normalize(e) for e in emails} - {""})
    out = {e: None for e in wanted}
    if not wanted:
        return out
    for i in range(0, len(wanted), _BATCH_IN):
        part = wanted[i:i + _BATCH_IN]
        marks = ",".join("?" * len(part))
        # No except: a timeout or lock here must reach the route as a 500, so the caller
        # fails closed. A swallowed error would answer "not blocked" for the whole batch.
        rows = cx.execute(f"SELECT email, bounce_type FROM email_suppression "
                          f"WHERE email IN ({marks})", part).fetchall()
        for r in rows:
            key = normalize(r[0])
            if key in out:
                out[key] = str(r[1] or "").strip() or "suppressed"
        rest = [e for e in part if out[e] is None]
        if not rest:
            continue
        marks = ",".join("?" * len(rest))
        rows = cx.execute(f"SELECT lower(email), tags FROM people "
                          f"WHERE lower(email) IN ({marks})", rest).fetchall()
        seen = {}
        for r in rows:
            seen.setdefault(normalize(r[0]), []).append(r[1])
        for em, all_tags in seen.items():
            if em in out and any(has_unsubscribed_tag(_tags_of(t)) for t in all_tags):
                out[em] = UNSUBSCRIBED_TAG
    return out


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
