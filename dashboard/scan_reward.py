"""Scan share reward: storage and plumbing. The rules live in scan_reward_rules.py.

Off unless SCAN_REWARD_ENABLED is set. Every entry point used by app.py checks
`enabled()` first, and the app checks it again before calling, so the flag off means
nothing here runs.

Pieces (plan: platform/plans/2026-11-06-scan-share-reward.md, build pieces 1-6):

* record_invite        a referral_events row when a friend gives an email on
                       /begin/scan with an inviter, no terms needed. Decision 2 hold.
* scan arrivals        "New voice scan" notices from E4L, read from Gmail (read only:
                       list and get, never a label or a read-state change).
* people.e4l_*         an E4L-account marker on the people hub, set from arrivals.
* reading_credits      an append-only ledger. Earn and spend rows, one per event key.
* credit_* functions   the inviter's reading on a friend's first scan or order.

Nothing here sends mail. `notify_inviter` is the hook for that, and it does nothing.
Prod is Postgres and tests are SQLite: no lastrowid, ON CONFLICT for idempotency.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from dashboard import scan_reward_rules as rules


def enabled():
    return (os.environ.get(rules.FLAG_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def _canonical(cx, email):
    e = _norm(email)
    try:
        from dashboard import person_aliases as _pal
        return _pal.canonical_email(cx, e) or e
    except Exception:
        return e


def _table_exists(cx, name):
    try:
        cx.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchall()
        return True
    except Exception:
        try:
            cx.rollback()     # Postgres: a failed statement poisons the transaction
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Tables
# ═══════════════════════════════════════════════════════════════════════════

def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS reading_credits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        event_key TEXT NOT NULL UNIQUE,
        friend_email TEXT DEFAULT '',
        inviter_slug TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_reading_credits_email "
               "ON reading_credits(email)")
    cx.execute("""CREATE TABLE IF NOT EXISTS scan_arrivals (
        gmail_msg_id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        client_name TEXT DEFAULT '',
        noticed_at TEXT NOT NULL,
        first_scan INTEGER NOT NULL DEFAULT 0,
        ingested_at TEXT NOT NULL
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_scan_arrivals_email "
               "ON scan_arrivals(email)")
    cx.commit()


def ensure_people_columns(cx):
    """The E4L-account field on the people hub. Two columns, because the notice gives
    us the fact of an account but not E4L's client id:

    * e4l_client_id   E4L's own id. Only the one-time fill from e4l.db sets it.
    * e4l_account_at  when we first learned this person holds an E4L account.

    Returns False when there is no people table (an isolated test)."""
    from dashboard import db
    if not _table_exists(cx, "people"):
        return False
    for col in ("e4l_client_id", "e4l_account_at"):
        if not db.column_exists(cx, "people", col):
            cx.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT")
    cx.commit()
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Piece 1: who invited the friend (decision 2 hold)
# ═══════════════════════════════════════════════════════════════════════════

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _approved_slugs(cx):
    try:
        return {r[0]: _norm(r[1]) for r in cx.execute(
            "SELECT slug, email FROM affiliate_signups WHERE status='approved'").fetchall()}
    except Exception:
        return {}


def _engagements(cx, friend_email, approved):
    rows = cx.execute(
        "SELECT utm_source, received_at FROM referral_events "
        "WHERE LOWER(email)=? AND COALESCE(utm_source,'')<>''",
        (_norm(friend_email),)).fetchall()
    return [(r[0], r[1]) for r in rows if r[0] in approved]


def _purchases(cx, friend_email):
    if not _table_exists(cx, "orders"):
        return []
    try:
        rows = cx.execute(
            "SELECT COALESCE(paid_at, created_at) FROM orders WHERE LOWER(email)=? "
            "AND (pay_status='paid' OR status IN ('paid','shipped','delivered','done')) "
            "AND status<>'cancelled'", (_norm(friend_email),)).fetchall()
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return []
    return [r[0] for r in rows if r[0]]


def holder_for(cx, friend_email, at=None):
    """Decision 2 applied to what we hold: the inviter that owns `friend_email` at
    `at` (default now), with their email. None when no one holds the friend."""
    approved = _approved_slugs(cx)
    if not approved:
        return None
    h = rules.holder_at(_engagements(cx, friend_email, approved),
                        _purchases(cx, friend_email), at or datetime.now(timezone.utc))
    if not h:
        return None
    return {**h, "inviter_email": approved.get(h["slug"], "")}


def record_invite(cx, friend_email, slug, *, first_name="", last_name=""):
    """Write the referral_events row for a friend who gave an email on /begin/scan.

    No terms acceptance is needed. A row is written only when no one holds the friend
    now; while a hold stands, first inviter wins and nothing is written. Self-referral
    is refused. Returns "recorded", "held", or a reason it did nothing."""
    friend = _norm(friend_email)
    slug = (slug or "").strip()
    if not friend or "@" not in friend:
        return "no-email"
    if not slug or not _SLUG_RE.match(slug):
        return "no-inviter"
    approved = _approved_slugs(cx)
    if slug not in approved:
        return "not-approved"
    if approved[slug] == friend:
        return "self"
    if holder_for(cx, friend) is not None:
        return "held"
    cx.execute("""INSERT INTO referral_events
          (received_at, lead_id, email, first_name, last_name,
           utm_source, utm_medium, utm_campaign, utm_content, utm_term, quiz_score, raw_json)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               (_now(), None, friend, first_name or "", last_name or "",
                slug, rules.REFERRAL_MEDIUM, rules.REFERRAL_CAMPAIGN, "", "", "", ""))
    cx.commit()
    return "recorded"


# ═══════════════════════════════════════════════════════════════════════════
# Piece 2: the scan link leads the ambassador hub
# ═══════════════════════════════════════════════════════════════════════════

HUB_SCAN_OFFER_NAME = "Free Bioenergetic Wellness Scan"
_ADVANCED_NOTE = ("Advanced direct link. The funnel links above preserve full "
                  "attribution across every step. ")


def lead_with_scan(offers):
    """Reorder hub offers so the /begin/scan link is first and marked primary.

    Read-side only: the affiliate_offers rows are untouched, so turning the flag off
    restores today's order exactly. The "advanced direct link" note the seed prepends
    is dropped from this one offer, since it is no longer below the funnel links."""
    offers = [dict(o) for o in (offers or [])]
    scan = [o for o in offers if o.get("name") == HUB_SCAN_OFFER_NAME]
    if not scan:
        return offers
    lead = scan[0]
    desc = lead.get("description") or ""
    if desc.startswith(_ADVANCED_NOTE):
        lead["description"] = desc[len(_ADVANCED_NOTE):]
    lead["primary"] = True
    return [lead] + [o for o in offers if o is not scan[0]]


# ═══════════════════════════════════════════════════════════════════════════
# Piece 5: the reading-credit ledger
# ═══════════════════════════════════════════════════════════════════════════

def _write(cx, *, email, delta, reason, event_key, friend_email="", inviter_slug="",
           detail="", created_by=""):
    """Append one ledger row. Idempotent on event_key. True if a row was written."""
    cur = cx.execute("""INSERT INTO reading_credits
          (email, delta, reason, event_key, friend_email, inviter_slug, detail,
           created_by, created_at)
          VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(event_key) DO NOTHING""",
                     (_canonical(cx, email), int(delta), reason, event_key,
                      _norm(friend_email), inviter_slug or "", detail or "",
                      created_by or "", _now()))
    wrote = (cur.rowcount or 0) > 0
    cx.commit()
    return wrote


def balance(cx, email):
    row = cx.execute("SELECT COALESCE(SUM(delta),0) FROM reading_credits WHERE email=?",
                     (_canonical(cx, email),)).fetchone()
    return int(row[0] or 0) if row else 0


def ledger(cx, email, limit=200):
    rows = cx.execute(
        "SELECT delta, reason, event_key, friend_email, inviter_slug, detail, "
        "created_by, created_at FROM reading_credits WHERE email=? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (_canonical(cx, email), int(limit))).fetchall()
    keys = ("delta", "reason", "event_key", "friend_email", "inviter_slug", "detail",
            "created_by", "created_at")
    return [dict(zip(keys, tuple(r))) for r in rows]


def portal_summary(cx, email):
    """What the client portal shows. None when this person has no ledger rows."""
    canon = _canonical(cx, email)
    row = cx.execute("SELECT COUNT(*), COALESCE(SUM(delta),0), "
                     "COALESCE(SUM(CASE WHEN delta>0 THEN delta ELSE 0 END),0) "
                     "FROM reading_credits WHERE email=?", (canon,)).fetchone()
    if not row or not int(row[0] or 0):
        return None
    return {"balance": int(row[1] or 0), "earned": int(row[2] or 0),
            "used": int(row[2] or 0) - int(row[1] or 0)}


def spend(cx, email, *, event_key, by, note=""):
    """Glen or Rae marks one reading delivered. Refuses when the balance is empty.
    Idempotent on event_key, so a double click spends once."""
    if not event_key:
        return {"ok": False, "error": "event_key required"}
    if cx.execute("SELECT 1 FROM reading_credits WHERE event_key=?",
                  (f"spend:{event_key}",)).fetchone():
        return {"ok": True, "spent": False, "duplicate": True,
                "balance": balance(cx, email)}
    if balance(cx, email) < 1:
        return {"ok": False, "error": "no reading credit to spend",
                "balance": balance(cx, email)}
    wrote = _write(cx, email=email, delta=-1, reason=rules.REASON_SPEND,
                   event_key=f"spend:{event_key}", detail=note, created_by=by)
    return {"ok": True, "spent": wrote, "balance": balance(cx, email)}


# ═══════════════════════════════════════════════════════════════════════════
# Piece 6: crediting the inviter
# ═══════════════════════════════════════════════════════════════════════════

def notify_inviter(inviter_email, *, reason, friend_email):
    """HOOK, deliberately empty. Telling the inviter they earned a reading is out of
    scope for this change: no client email is sent. Wire a send_bulk call here later,
    after its own three review rounds."""
    return None


def _credit(cx, friend_email, at, *, readings, reason, event_key, detail=""):
    holder = holder_for(cx, friend_email, at)
    if not holder or not holder.get("inviter_email"):
        return {"credited": False, "why": "no-holder"}
    if holder["inviter_email"] == _norm(friend_email):
        return {"credited": False, "why": "self"}
    wrote = _write(cx, email=holder["inviter_email"], delta=readings, reason=reason,
                   event_key=event_key, friend_email=friend_email,
                   inviter_slug=holder["slug"], detail=detail, created_by="system")
    if wrote:
        notify_inviter(holder["inviter_email"], reason=reason, friend_email=friend_email)
    return {"credited": wrote, "why": "credited" if wrote else "duplicate",
            "inviter": holder["slug"]}


def credit_first_scan(cx, friend_email, scanned_at):
    return _credit(cx, friend_email, scanned_at,
                   readings=rules.READINGS_PER_FIRST_SCAN,
                   reason=rules.REASON_FIRST_SCAN,
                   event_key=rules.event_key_first_scan(friend_email))


def credit_store_order(cx, order, order_ref, *, catalog=None, is_wholesale=False):
    """One reading to the friend's inviter for a paid store order with an E4L
    infoceutical or PRL line. Idempotent per order_ref."""
    order = order or {}
    friend = _norm(order.get("email"))
    if not friend or not order_ref:
        return {"credited": False, "why": "no-email-or-ref"}
    items = order.get("items")
    if items is None:
        import json
        try:
            items = json.loads(order.get("items_json") or "[]")
        except Exception:
            items = []
    if catalog is None:
        from dashboard import products as _products
        catalog = _products._cached_products()
    if not rules.store_order_qualifies(items, catalog, is_wholesale=is_wholesale):
        return {"credited": False, "why": "not-qualifying"}
    at = order.get("paid_at") or _now()
    return _credit(cx, friend, at, readings=rules.READINGS_PER_QUALIFYING_ORDER,
                   reason=rules.REASON_STORE_ORDER,
                   event_key=rules.event_key_store_order(order_ref), detail=order_ref)


def credit_e4l_direct_order(cx, friend_email, *, message_id, ordered_at):
    """HOOK for "E4L Portal - Client's Purchase Notification" emails. Nothing parses
    those yet (verified 6 Nov 2026), so nothing calls this. A future parser passes
    the client email and the Gmail message id here."""
    return _credit(cx, friend_email, ordered_at,
                   readings=rules.READINGS_PER_QUALIFYING_ORDER,
                   reason=rules.REASON_E4L_ORDER,
                   event_key=rules.event_key_e4l_order(message_id), detail=message_id)


def credit_prl_order(cx, friend_email, *, order_ref, ordered_at):
    """HOOK for a PRL order under code 021a1a. No source exists yet: Glen, 6 Nov 2026,
    "We haven't had any yet." Nothing calls this."""
    return _credit(cx, friend_email, ordered_at,
                   readings=rules.READINGS_PER_QUALIFYING_ORDER,
                   reason=rules.REASON_PRL_ORDER,
                   event_key=rules.event_key_prl_order(order_ref), detail=order_ref)


# ═══════════════════════════════════════════════════════════════════════════
# Pieces 3 and 4: scan arrivals from E4L "New voice scan" notices
# ═══════════════════════════════════════════════════════════════════════════

SCAN_SENDER = "noreply@e4l.com"
SCAN_SUBJECTS = ("New voice scan", "New remote scan", "New BWS scan")
SCAN_GMAIL_QUERY = (f"from:{SCAN_SENDER} subject:("
                    + " OR ".join(f'"{s}"' for s in SCAN_SUBJECTS) + ")")
# How far back one run looks. The daily cron only needs the last day or two. Kept
# short so the first run does not treat months of old notices as arrivals.
SCAN_NOTICE_LOOKBACK_DAYS = 14
# The same pattern as 02 Skills/gmail-e4l-check.py: "...waiting for you from Ann
# Bauder (ann@example.com)". The address resolves the client without guessing by name.
_SCAN_BODY_RE = re.compile(
    r"waiting for you from\s+(.+?)\s*\(\s*([^()\s]+@[^()\s]+)\s*\)", re.IGNORECASE)


def parse_scan_notice(subject, body):
    """{"email", "client_name"} from a notice, or None when it is not one."""
    if not any(s.lower() in (subject or "").lower() for s in SCAN_SUBJECTS):
        return None
    m = _SCAN_BODY_RE.search(re.sub(r"\s+", " ", body or ""))
    if not m:
        return None
    return {"email": m.group(2).strip().lower().rstrip(".,;"),
            "client_name": m.group(1).strip()}


def _had_scan_before(cx, email, noticed_at):
    """Any record of a scan for this person before this notice. A first scan is the
    first EVER, so every store we hold is asked."""
    if cx.execute("SELECT 1 FROM scan_arrivals WHERE email=? LIMIT 1",
                  (email,)).fetchone():
        return True
    day = (noticed_at or "")[:10]
    for sql in ("SELECT 1 FROM scan_freshness WHERE LOWER(email)=? AND last_scan_date<? LIMIT 1",
                "SELECT 1 FROM client_scans WHERE LOWER(email)=? AND scan_date<? LIMIT 1"):
        try:
            if cx.execute(sql, (email, day)).fetchone():
                return True
        except Exception:
            try:
                cx.rollback()
            except Exception:
                pass
    return False


def mark_e4l_account(cx, email, seen_at):
    """Set people.e4l_account_at on an existing person, once. Never creates a person.
    Returns True when a row was updated."""
    if not ensure_people_columns(cx):
        return False
    cur = cx.execute("UPDATE people SET e4l_account_at=? WHERE email=? "
                     "AND (e4l_account_at IS NULL OR e4l_account_at='')",
                     (seen_at, _canonical(cx, email)))
    cx.commit()
    return (cur.rowcount or 0) > 0


def record_scan_arrival(cx, *, msg_id, email, client_name, noticed_at):
    """Store one arrival and act on it. Idempotent per Gmail message id.
    Returns "duplicate", "repeat" or "first"."""
    email = _canonical(cx, email)
    if cx.execute("SELECT 1 FROM scan_arrivals WHERE gmail_msg_id=?",
                  (msg_id,)).fetchone():
        return "duplicate"
    first = not _had_scan_before(cx, email, noticed_at)
    cx.execute("""INSERT INTO scan_arrivals
          (gmail_msg_id, email, client_name, noticed_at, first_scan, ingested_at)
          VALUES (?,?,?,?,?,?) ON CONFLICT(gmail_msg_id) DO NOTHING""",
               (msg_id, email, client_name or "", noticed_at, int(first), _now()))
    cx.commit()
    mark_e4l_account(cx, email, noticed_at)
    if first:
        credit_first_scan(cx, email, noticed_at)
    return "first" if first else "repeat"


def _notice_time(message):
    try:
        stamp = int(message.get("internalDate") or 0) / 1000
        if stamp:
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        pass
    return _now()


def ingest_scan_notices(cx, *, service=None, max_messages=200, now=None):
    """Read new "New voice scan" notices from Gmail and record each arrival.

    Gmail is used read only: messages.list and messages.get. No label, no read-state
    change, no send. No E4L portal traffic. Oldest first, so of two notices for one
    person in a run the earlier one is the first scan."""
    from dashboard import inbox
    init_tables(cx)
    if service is None:
        service = inbox._get_gmail_service()
    since = int(((now or datetime.now(timezone.utc))
                 - timedelta(days=SCAN_NOTICE_LOOKBACK_DAYS)).timestamp())
    q = f"{SCAN_GMAIL_QUERY} after:{since}"
    listed, token = [], None
    while len(listed) < max_messages:
        kwargs = {"userId": "me", "q": q, "maxResults": min(100, max_messages - len(listed))}
        if token:
            kwargs["pageToken"] = token
        page = service.users().messages().list(**kwargs).execute()
        listed.extend(page.get("messages") or [])
        token = page.get("nextPageToken")
        if not token:
            break

    fetched = []
    counts = {"listed": len(listed), "first": 0, "repeat": 0, "duplicate": 0,
              "parse_failed": 0, "skipped": 0}
    for ref in listed:
        msg_id = ref.get("id")
        if not msg_id:
            counts["skipped"] += 1
            continue
        if cx.execute("SELECT 1 FROM scan_arrivals WHERE gmail_msg_id=?",
                      (msg_id,)).fetchone():
            counts["duplicate"] += 1
            continue
        message = service.users().messages().get(
            userId="me", id=msg_id, format="full").execute()
        headers = (message.get("payload") or {}).get("headers") or []
        if SCAN_SENDER not in inbox._header(headers, "From").lower():
            counts["skipped"] += 1
            continue
        fetched.append((_notice_time(message), msg_id, message, headers))
    fetched.sort(key=lambda f: f[0])
    for noticed_at, msg_id, message, headers in fetched:
        body = inbox._extract_body(message.get("payload") or {})["plain"]
        parsed = parse_scan_notice(inbox._header(headers, "Subject"), body)
        if not parsed:
            counts["parse_failed"] += 1
            continue
        outcome = record_scan_arrival(cx, msg_id=msg_id, email=parsed["email"],
                                      client_name=parsed["client_name"],
                                      noticed_at=noticed_at)
        counts[outcome] += 1
    return counts


# ═══════════════════════════════════════════════════════════════════════════
# Piece 4, one-time fill: E4L client ids from e4l.db (a production write)
# ═══════════════════════════════════════════════════════════════════════════

def fill_e4l_client_ids(cx, rows, *, apply=False):
    """rows: [{"email", "client_id"}]. Sets people.e4l_client_id on matching people
    rows (after alias resolution) where it is empty. Never overwrites, never creates
    a person. apply=False counts only."""
    if not ensure_people_columns(cx):
        return {"ok": False, "error": "no people table"}
    out = {"rows": 0, "matched": 0, "already_set": 0, "would_set": 0, "set": 0,
           "unmatched": 0, "bad": 0, "applied": bool(apply)}
    now = _now()
    for r in rows or []:
        out["rows"] += 1
        email = _canonical(cx, r.get("email"))
        cid = str(r.get("client_id") or "").strip()
        if not email or "@" not in email or not cid:
            out["bad"] += 1
            continue
        row = cx.execute("SELECT e4l_client_id FROM people WHERE email=?",
                         (email,)).fetchone()
        if not row:
            out["unmatched"] += 1
            continue
        out["matched"] += 1
        if (row[0] or "").strip():
            out["already_set"] += 1
            continue
        out["would_set"] += 1
        if apply:
            cur = cx.execute(
                "UPDATE people SET e4l_client_id=?, "
                "e4l_account_at=COALESCE(NULLIF(e4l_account_at,''), ?) "
                "WHERE email=? AND (e4l_client_id IS NULL OR e4l_client_id='')",
                (cid, now, email))
            out["set"] += int((cur.rowcount or 0) > 0)
    if apply:
        cx.commit()
    return out
