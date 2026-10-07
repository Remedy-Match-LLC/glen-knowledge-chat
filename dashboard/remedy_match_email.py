"""The chat's "Your remedy match" email, rebuilt so it cannot misspeak or flood.

The first version (#1395, 2026-08-22) emailed at once, after every chat reply an AI judged
to have settled on a product, carrying that AI's own unreviewed sentence. One client got
12 in 31 hours, naming 11 things, with internal notes and health claims in Glen's name.
Glen switched it off on 2026-09-22 and chose, for the rebuild, to send automatically under
these rules (platform/plans/2026-09-22-remedy-match-email-plan):

  1. No AI-written text. The email carries only the product's name and its page link.
  2. Catalog products only: an active product with a page. Tools, services, outside
     brands and scans never qualify; the caller resolves that before enqueueing.
  3. One email per chat, sent after the chat has been quiet for QUIET_MINUTES. A chat
     that moves between options just replaces its pending match; only the last is sent.
  4. At most one per client per CAP_DAYS.
  5. Every send is recorded with its exact body.
  6. Confirm first (Glen, 2026-10-07). An address not yet proven (a portal sign-in proves
     it) waits as 'awaiting_confirm'. The drain mails one confirmation link, without the
     product name; clicking it confirms the address for good and releases the match.
     A match whose address is not confirmed within CONFIRM_DAYS is dropped.

Sending claims a row with one atomic UPDATE before mailing, so two scheduler processes
running the drain at once cannot both send it.
"""
from datetime import datetime, timedelta, timezone

QUIET_MINUTES = 30
CAP_DAYS = 7
CONFIRM_DAYS = 7
CONFIRM_RESEND_HOURS = 24

# Wording: Glen, 2026-09-23. Not "remedy match": that is the free scan report's name.
SUBJECT = "The remedy you found in our chat: {product}"
TEXT = ("Aloha{name},\n\n"
        "Here's a link so you can explore the remedy you found in our chat:\n"
        "{product}\n{url}\n\n"
        "Aloha,\nDr. Glen")
HTML = ('<div style="font-family: \'arial black\', sans-serif; font-size: large">'
        "<p>Aloha{name},</p>"
        "<p>Here's a link so you can explore the remedy you found in our chat:<br>"
        '<a href="{url}">{product}</a></p>'
        "<p>Aloha,<br>Dr. Glen</p></div>")


# Confirmation wording: Glen, 2026-10-07 ("yes" to the draft). No product name in it.
CONFIRM_SUBJECT = "Confirm your email to get your remedy link"
CONFIRM_TEXT = ("Aloha{name},\n\n"
                "Please confirm this is your email, and I'll send you the link to the remedy "
                "you found in our chat:\n{url}\n\n"
                "If you didn't chat with us, you can ignore this message.\n\n"
                "Aloha,\nDr. Glen")
CONFIRM_HTML = ('<div style="font-family: \'arial black\', sans-serif; font-size: large">'
                "<p>Aloha{name},</p>"
                "<p>Please confirm this is your email, and I'll send you the link to the remedy "
                "you found in our chat:</p>"
                '<p><a href="{url}">Confirm my email</a></p>'
                "<p>If you didn't chat with us, you can ignore this message.</p>"
                "<p>Aloha,<br>Dr. Glen</p></div>")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def init(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS remedy_match_email_queue (
        email TEXT NOT NULL, session_id TEXT NOT NULL, name TEXT,
        product_slug TEXT NOT NULL, product_name TEXT NOT NULL, page_url TEXT NOT NULL,
        updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        sent_at TEXT, reason TEXT, subject TEXT, body_text TEXT,
        PRIMARY KEY (email, session_id))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS remedy_match_email_confirm (
        email TEXT PRIMARY KEY, token_hash TEXT, sent_at TEXT, confirmed_at TEXT)""")
    # One row per link sent, so a resend never cancels a link already delivered.
    cx.execute("""CREATE TABLE IF NOT EXISTS remedy_match_email_confirm_tokens (
        token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, sent_at TEXT NOT NULL)""")
    cx.commit()


def _hash(token):
    import hashlib
    return hashlib.sha256((token or "").encode()).hexdigest()


def is_confirmed(cx, email):
    init(cx)
    row = cx.execute("SELECT confirmed_at FROM remedy_match_email_confirm WHERE email=?",
                     ((email or "").strip().lower(),)).fetchone()
    return bool(row and row[0])


def mark_confirmed(cx, email, now=None):
    """Record an address as proven (a portal sign-in, or a clicked link) and release its
    waiting matches into the normal queue."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return
    init(cx)
    stamp = _iso(now or _now())
    cx.execute("INSERT INTO remedy_match_email_confirm (email, confirmed_at) VALUES (?,?) "
               "ON CONFLICT(email) DO UPDATE SET confirmed_at=excluded.confirmed_at "
               "WHERE remedy_match_email_confirm.confirmed_at IS NULL",
               (email, stamp))
    _release(cx, now or _now(), email)
    cx.commit()


def _release(cx, now, email=None):
    """Move waiting matches of confirmed addresses into the normal queue, but only those
    still inside CONFIRM_DAYS. With no email, every confirmed address: this also heals a
    match written as waiting while another process was confirming its address."""
    fresh = _iso(now - timedelta(days=CONFIRM_DAYS))
    sql = ("UPDATE remedy_match_email_queue SET status='pending' "
           "WHERE status='awaiting_confirm' AND updated_at > ? AND email IN "
           "(SELECT email FROM remedy_match_email_confirm WHERE confirmed_at IS NOT NULL)")
    args = [fresh]
    if email:
        sql += " AND email=?"
        args.append(email)
    cx.execute(sql, tuple(args))


def confirm(cx, token, now=None):
    """The address a confirmation link belongs to, now confirmed; None when the token is
    unknown or older than CONFIRM_DAYS."""
    if not token:
        return None
    init(cx)
    now = now or _now()
    row = cx.execute("SELECT email, sent_at FROM remedy_match_email_confirm_tokens "
                     "WHERE token_hash=?", (_hash(token),)).fetchone()
    if not row:
        return None
    email, sent_at = row[0], row[1]
    if not is_confirmed(cx, email):
        try:
            if now - datetime.fromisoformat(sent_at) > timedelta(days=CONFIRM_DAYS):
                return None
        except (TypeError, ValueError):
            return None
    mark_confirmed(cx, email, now=now)
    return email


def enqueue(cx, *, email, name, session_id, product_slug, product_name, page_url,
            proven=False, now=None):
    """Record (or replace) this chat's pending match. A chat already sent or skipped is
    left alone: one email per chat. `proven` says the caller knows the address belongs to
    the visitor (a portal sign-in). Otherwise the row waits for confirmation unless the
    address was confirmed before. Returns True when a row now holds the match."""
    email = (email or "").strip().lower()
    session_id = (session_id or "").strip()
    if "@" not in email or not session_id or not product_slug or not page_url:
        return False
    init(cx)
    if proven:
        mark_confirmed(cx, email, now=now)
    status = "pending" if (proven or is_confirmed(cx, email)) else "awaiting_confirm"
    stamp = _iso(now or _now())
    cur = cx.execute(
        "INSERT INTO remedy_match_email_queue (email, session_id, name, product_slug, "
        "product_name, page_url, updated_at, status) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(email, session_id) DO UPDATE SET name=excluded.name, "
        "product_slug=excluded.product_slug, product_name=excluded.product_name, "
        "page_url=excluded.page_url, updated_at=excluded.updated_at, "
        "status=CASE WHEN excluded.status='pending' THEN 'pending' "
        "ELSE remedy_match_email_queue.status END "
        "WHERE remedy_match_email_queue.status IN ('pending', 'awaiting_confirm')",
        (email, session_id, (name or "").strip(), product_slug, product_name, page_url,
         stamp, status))
    cx.commit()
    return bool(cur.rowcount)


def render_confirm(name, url):
    first = (name or "").strip().split(" ")[0]
    nm = (" " + first) if first else ""
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    return (CONFIRM_SUBJECT, CONFIRM_HTML.format(name=esc(nm), url=esc(url)),
            CONFIRM_TEXT.format(name=nm, url=url))


def _confirmations(cx, send_fn, confirm_url, now):
    """Mail one confirmation link per waiting address, at most once per
    CONFIRM_RESEND_HOURS, and drop waiting matches older than CONFIRM_DAYS."""
    out = {"confirm_sent": 0, "confirm_failed": 0, "expired": 0}
    stale = _iso(now - timedelta(days=CONFIRM_DAYS))
    cur = cx.execute("UPDATE remedy_match_email_queue SET status='skipped', "
                     "reason='address not confirmed' "
                     "WHERE status='awaiting_confirm' AND updated_at <= ?", (stale,))
    cx.commit()
    out["expired"] = cur.rowcount or 0
    _release(cx, now)
    cx.commit()
    if confirm_url is None:
        return out
    resend = _iso(now - timedelta(hours=CONFIRM_RESEND_HOURS))
    for row in _rows(cx.execute(
            "SELECT q.email, MAX(q.name) AS name FROM remedy_match_email_queue q "
            "LEFT JOIN remedy_match_email_confirm c ON c.email = q.email "
            "WHERE q.status='awaiting_confirm' AND c.confirmed_at IS NULL "
            "AND (c.sent_at IS NULL OR c.sent_at <= ?) GROUP BY q.email", (resend,))):
        import secrets
        token = secrets.token_urlsafe(24)
        prev = cx.execute("SELECT sent_at FROM remedy_match_email_confirm WHERE email=?",
                          (row["email"],)).fetchone()
        claim = cx.execute(
            "INSERT INTO remedy_match_email_confirm (email, token_hash, sent_at) "
            "VALUES (?,?,?) ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash, "
            "sent_at=excluded.sent_at WHERE remedy_match_email_confirm.confirmed_at IS NULL "
            "AND (remedy_match_email_confirm.sent_at IS NULL "
            "OR remedy_match_email_confirm.sent_at <= ?)",
            (row["email"], _hash(token), _iso(now), resend))
        cx.commit()
        if claim.rowcount != 1:
            continue                       # another process took it
        cx.execute("INSERT INTO remedy_match_email_confirm_tokens (token_hash, email, sent_at) "
                   "VALUES (?,?,?)", (_hash(token), row["email"], _iso(now)))
        cx.commit()
        subject, html, text = render_confirm(row.get("name"), confirm_url(token))
        try:
            send_fn(row["email"], row.get("name") or "", subject, html, text)
            out["confirm_sent"] += 1
        except Exception:
            # Nothing reached them: drop this link and restore the resend timer, so the
            # next drain tries again rather than waiting a day.
            cx.execute("DELETE FROM remedy_match_email_confirm_tokens WHERE token_hash=?",
                       (_hash(token),))
            cx.execute("UPDATE remedy_match_email_confirm SET sent_at=? WHERE email=?",
                       (prev[0] if prev else None, row["email"]))
            cx.commit()
            out["confirm_failed"] += 1
    return out


def render(row):
    first = (row.get("name") or "").strip().split(" ")[0]
    name = (" " + first) if first else ""
    url = row["page_url"]
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    return (SUBJECT.format(product=row["product_name"]),
            HTML.format(name=esc(name), product=esc(row["product_name"]), url=esc(url)),
            TEXT.format(name=name, product=row["product_name"], url=url))


def _rows(cur):
    rows = cur.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [{k: r[k] for k in r.keys()} for r in rows]
    cols = [d[0] for d in (getattr(cur, "description", None) or [])]
    return [dict(zip(cols, r)) for r in rows]


def drain(cx, send_fn, *, confirm_url=None, now=None):
    """Send every pending match whose chat has been quiet for QUIET_MINUTES, and one
    confirmation link per waiting address. confirm_url(token) builds the link.
    send_fn(email, name, subject, html, text) raises on failure. Returns counts."""
    init(cx)
    now = now or _now()
    due = _iso(now - timedelta(minutes=QUIET_MINUTES))
    out = {"sent": 0, "capped": 0, "failed": 0}
    out.update(_confirmations(cx, send_fn, confirm_url, now))
    for row in _rows(cx.execute(
            "SELECT email, session_id, name, product_slug, product_name, page_url "
            "FROM remedy_match_email_queue WHERE status='pending' AND updated_at <= ? "
            "ORDER BY updated_at", (due,))):
        since = _iso(now - timedelta(days=CAP_DAYS))
        recent = cx.execute(
            "SELECT 1 FROM remedy_match_email_queue WHERE email=? AND status='sent' "
            "AND sent_at >= ? LIMIT 1", (row["email"], since)).fetchone()
        if recent:
            cx.execute("UPDATE remedy_match_email_queue SET status='skipped', reason=? "
                       "WHERE email=? AND session_id=? AND status='pending'",
                       (f"one per {CAP_DAYS} days", row["email"], row["session_id"]))
            cx.commit()
            out["capped"] += 1
            continue
        claim = cx.execute(
            "UPDATE remedy_match_email_queue SET status='sending' "
            "WHERE email=? AND session_id=? AND status='pending'",
            (row["email"], row["session_id"]))
        cx.commit()
        if claim.rowcount != 1:
            continue                       # another process took it
        subject, html, text = render(row)
        try:
            send_fn(row["email"], row.get("name") or "", subject, html, text)
        except Exception as exc:
            cx.execute("UPDATE remedy_match_email_queue SET status='failed', reason=? "
                       "WHERE email=? AND session_id=?",
                       (f"{type(exc).__name__}: {exc}"[:300], row["email"],
                        row["session_id"]))
            cx.commit()
            out["failed"] += 1
            continue
        cx.execute("UPDATE remedy_match_email_queue SET status='sent', sent_at=?, "
                   "subject=?, body_text=? WHERE email=? AND session_id=?",
                   (_iso(now), subject, text, row["email"], row["session_id"]))
        cx.commit()
        out["sent"] += 1
    return out


def recent(cx, limit=100):
    init(cx)
    return _rows(cx.execute(
        "SELECT email, session_id, product_name, page_url, status, updated_at, sent_at, "
        "reason, subject, body_text FROM remedy_match_email_queue "
        "ORDER BY updated_at DESC LIMIT ?", (int(limit),)))
