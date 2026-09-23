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

Sending claims a row with one atomic UPDATE before mailing, so two scheduler processes
running the drain at once cannot both send it.
"""
from datetime import datetime, timedelta, timezone

QUIET_MINUTES = 30
CAP_DAYS = 7

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
    cx.commit()


def enqueue(cx, *, email, name, session_id, product_slug, product_name, page_url, now=None):
    """Record (or replace) this chat's pending match. A chat already sent or skipped is
    left alone: one email per chat. Returns True when a pending row now holds it."""
    email = (email or "").strip().lower()
    session_id = (session_id or "").strip()
    if "@" not in email or not session_id or not product_slug or not page_url:
        return False
    init(cx)
    stamp = _iso(now or _now())
    cur = cx.execute(
        "INSERT INTO remedy_match_email_queue (email, session_id, name, product_slug, "
        "product_name, page_url, updated_at, status) VALUES (?,?,?,?,?,?,?,'pending') "
        "ON CONFLICT(email, session_id) DO UPDATE SET name=excluded.name, "
        "product_slug=excluded.product_slug, product_name=excluded.product_name, "
        "page_url=excluded.page_url, updated_at=excluded.updated_at "
        "WHERE remedy_match_email_queue.status='pending'",
        (email, session_id, (name or "").strip(), product_slug, product_name, page_url,
         stamp))
    cx.commit()
    return bool(cur.rowcount)


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


def drain(cx, send_fn, *, now=None):
    """Send every pending match whose chat has been quiet for QUIET_MINUTES.
    send_fn(email, name, subject, html, text) raises on failure. Returns counts."""
    init(cx)
    now = now or _now()
    due = _iso(now - timedelta(minutes=QUIET_MINUTES))
    out = {"sent": 0, "capped": 0, "failed": 0}
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
