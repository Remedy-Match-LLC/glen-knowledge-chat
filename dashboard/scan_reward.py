"""Scan share reward: storage and plumbing. The rules live in scan_reward_rules.py.

Off unless SCAN_REWARD_ENABLED is set. Every entry point used by app.py checks
`enabled()` first, so the flag off means nothing here runs.

Pieces (plan: platform/plans/2026-11-06-scan-share-reward.md, build pieces 1-6):

* scan_invites          who invited a friend. Its OWN table: System A's payout reads
                        referral_events and never sees these rows.
* scan_arrivals         "New voice scan" notices from E4L, read from Gmail (list and
                        get only: never a label, a read-state change or a send).
* people.e4l_*          an E4L-account marker on the people hub.
* reading_credits       append-only ledger of earn and spend rows, one per event key.
* reading_credit_outcomes  the decision taken for each event key, credited or not, so
                        a retry can never decide differently later.

Identity: every lookup goes through `identity_emails`, the canonical address plus
every alias that maps to it. Ledger rows are owned by the canonical address.

Nothing here sends mail. `notify_inviter` is the hook for that, and it does nothing.
Prod is Postgres and tests are SQLite: no lastrowid, ON CONFLICT for idempotency,
reads that can fail run before any write in the same transaction.
"""
from __future__ import annotations

import json
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


def _backend(cx):
    from dashboard import db
    return db.backend_of(cx)


def _has_table(cx, name):
    """Never raises and never poisons a Postgres transaction."""
    if _backend(cx) == "postgres":
        return cx.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=?", (name,)).fetchone() is not None
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _has_column(cx, table, col):
    from dashboard import db
    return _has_table(cx, table) and db.column_exists(cx, table, col)


def _lock(cx, key):
    """Serialise writers for one key. Postgres: a transaction-scoped advisory lock.
    SQLite: the app's _db_lock and SQLite's single writer already serialise."""
    if _backend(cx) == "postgres":
        cx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


def _in(values):
    vals = sorted(values)
    return ",".join("?" * len(vals)), tuple(vals)


# ═══════════════════════════════════════════════════════════════════════════
# Identity
# ═══════════════════════════════════════════════════════════════════════════

def canonical(cx, email):
    e = _norm(email)
    if not e or not _has_table(cx, "email_aliases"):
        return e
    from dashboard import person_aliases as _pal
    return _pal.canonical_email(cx, e) or e


def identity_emails(cx, email):
    """The canonical address and every alias that resolves to it."""
    canon = canonical(cx, email)
    out = {e for e in (canon, _norm(email)) if e}
    if not canon or not _has_table(cx, "email_aliases"):
        return out
    frontier = [canon]
    while frontier and len(out) < 500:
        e = frontier.pop()
        for (a,) in cx.execute("SELECT alias_email FROM email_aliases WHERE canonical_email=?",
                               (e,)).fetchall():
            a = _norm(a)
            if a and a not in out:
                out.add(a)
                frontier.append(a)
    return out


_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def gmail_key(email):
    """One key per Gmail mailbox: Gmail ignores dots and anything after a plus in
    the local part, and googlemail.com is the same service. Other addresses are
    returned as they are."""
    e = _norm(email)
    local, _, dom = e.rpartition("@")
    if dom in _GMAIL_DOMAINS and local:
        return local.split("+", 1)[0].replace(".", "") + "@gmail.com"
    return e


# Where a person may already appear under another spelling of their Gmail address.
_VARIANT_SOURCES = (("referral_events", "email"), ("orders", "email"),
                    ("scan_freshness", "email"), ("client_scans", "email"),
                    ("scan_arrivals", "email"), ("scan_invites", "friend_email"))


def variant_emails(cx, ids):
    """`ids` plus every stored address that is the same Gmail mailbox as one of
    them. Narrowed in SQL by first letter and domain, then matched exactly in Python
    on gmail_key, so it works the same on SQLite and Postgres."""
    keys = {gmail_key(i) for i in ids if _norm(i).rpartition("@")[2] in _GMAIL_DOMAINS}
    out = set(ids)
    if not keys:
        return out
    for table, col in _VARIANT_SOURCES:
        if not _has_table(cx, table):
            continue
        for k in keys:
            c = k[0]
            for (e,) in cx.execute(
                    f"SELECT DISTINCT LOWER({col}) FROM {table} "
                    f"WHERE LOWER({col}) LIKE ? OR LOWER({col}) LIKE ?",
                    (c + "%@gmail.com", c + "%@googlemail.com")).fetchall():
                if e and gmail_key(e) == k:
                    out.add(_norm(e))
    return out


def _same_person(a_ids, b_ids):
    return bool({gmail_key(x) for x in a_ids} & {gmail_key(x) for x in b_ids})


# ═══════════════════════════════════════════════════════════════════════════
# Tables
# ═══════════════════════════════════════════════════════════════════════════

def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS scan_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        friend_email TEXT NOT NULL,
        slug TEXT NOT NULL,
        inviter_email TEXT NOT NULL,
        engaged_at TEXT NOT NULL,
        source TEXT DEFAULT ''
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_scan_invites_friend "
               "ON scan_invites(friend_email)")
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
    cx.execute("""CREATE TABLE IF NOT EXISTS reading_credit_outcomes (
        event_key TEXT PRIMARY KEY,
        outcome TEXT NOT NULL,
        detail TEXT DEFAULT '',
        decided_at TEXT NOT NULL
    )""")
    cx.execute("""CREATE TABLE IF NOT EXISTS scan_arrivals (
        gmail_msg_id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        client_name TEXT DEFAULT '',
        noticed_at TEXT NOT NULL,
        history TEXT NOT NULL,
        ingested_at TEXT NOT NULL
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_scan_arrivals_email "
               "ON scan_arrivals(email)")
    cx.commit()


def ensure_people_columns(cx):
    """The E4L-account field on the people hub. Two columns, because a notice gives
    the fact of an account but not E4L's client id:

    * e4l_client_id   E4L's own id. Only the one-time fill from e4l.db sets it.
    * e4l_account_at  when we first learned this person holds an E4L account.

    Returns False when there is no people table."""
    if not _has_table(cx, "people"):
        return False
    changed = False
    for col in ("e4l_client_id", "e4l_account_at"):
        if not _has_column(cx, "people", col):
            cx.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT")
            changed = True
    if changed:
        cx.commit()
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Piece 1: who invited the friend (decision 2 hold)
# ═══════════════════════════════════════════════════════════════════════════

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _approved_slugs(cx):
    if not _has_table(cx, "affiliate_signups"):
        return {}
    return {r[0]: canonical(cx, r[1]) for r in cx.execute(
        "SELECT slug, email FROM affiliate_signups WHERE status='approved'").fetchall()}


def _engagements(cx, ids, approved):
    ph, vals = _in(ids)
    rows = cx.execute(f"SELECT slug, engaged_at FROM scan_invites "
                      f"WHERE friend_email IN ({ph})", vals).fetchall()
    return [(r[0], r[1]) for r in rows if r[0] in approved]


def _purchase_times(cx, ids):
    """Stable purchase time per paid order: paid_at, else created_at."""
    if not _has_table(cx, "orders"):
        return []
    ph, vals = _in(ids)
    rows = cx.execute(
        f"SELECT COALESCE(NULLIF(paid_at,''), created_at) FROM orders "
        f"WHERE LOWER(email) IN ({ph}) "
        f"AND (pay_status='paid' OR status IN ('paid','shipped','delivered','done')) "
        f"AND COALESCE(status,'')<>'cancelled'", vals).fetchall()
    return [r[0] for r in rows if r[0]]


def holder_for(cx, friend_email, at=None, ids=None):
    """Decision 2 applied to what we hold: the inviter owning `friend_email` at `at`
    (default now), with their canonical email. None when no one holds the friend."""
    approved = _approved_slugs(cx)
    if not approved:
        return None
    ids = ids or identity_emails(cx, friend_email)
    if not ids:
        return None
    h = rules.holder_at(_engagements(cx, ids, approved), _purchase_times(cx, ids),
                        at or datetime.now(timezone.utc))
    if not h:
        return None
    return {**h, "inviter_email": approved.get(h["slug"], "")}


def _known_customer(cx, ids):
    """Why this person may not be claimed as a new friend, or None.

    An invite comes from an email typed into a public page, so it proves nothing.
    It may only name someone we know nothing about: no System A referral, no paid
    order and no scan on record, under any of their addresses."""
    ph, vals = _in(ids)
    if _has_table(cx, "referral_events") and cx.execute(
            f"SELECT 1 FROM referral_events WHERE LOWER(email) IN ({ph}) "
            f"AND COALESCE(utm_source,'')<>'' LIMIT 1", vals).fetchone():
        return "known-referral"
    if _purchase_times(cx, ids):
        return "known-customer"
    if scan_history(cx, ids, None) != "first":
        return "known-scanner"
    return None


def record_invite(cx, friend_email, slug):
    """Record a scan-share invite on `cx`, its own connection.

    Unverified by design: the email comes from the request body. So it records only
    a person unknown to us (see _known_customer), never a self-referral under any
    alias, and never while another inviter holds the friend. Returns "recorded" or
    the reason it did nothing. Raises only on a database error."""
    raw = _norm(friend_email)
    slug = (slug or "").strip()
    if not raw or "@" not in raw:
        return "no-email"
    if not slug or not _SLUG_RE.match(slug):
        return "no-inviter"
    approved = _approved_slugs(cx)
    if slug not in approved:
        return "not-approved"
    friend = canonical(cx, raw)
    # Aliases, then every stored spelling of the same Gmail mailbox.
    ids = variant_emails(cx, identity_emails(cx, raw))
    if _same_person(ids, identity_emails(cx, approved[slug])):
        return "self"
    why = _known_customer(cx, ids)
    if why:
        return why
    _lock(cx, f"scan-invite:{friend}")
    if holder_for(cx, friend, ids=ids) is not None:
        cx.commit()
        return "held"
    cx.execute("INSERT INTO scan_invites (friend_email, slug, inviter_email, engaged_at, "
               "source) VALUES (?,?,?,?,?)",
               (friend, slug, approved[slug], _now(), rules.INVITE_SOURCE))
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
    Read-side only: affiliate_offers is untouched."""
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
# Piece 5: the ledger and the portal card
# ═══════════════════════════════════════════════════════════════════════════

def balance(cx, email):
    """Under every alias of the person, so rows written before a merge still count."""
    ph, vals = _in(identity_emails(cx, email))
    if not vals:
        return 0
    row = cx.execute(f"SELECT COALESCE(SUM(delta),0) FROM reading_credits "
                     f"WHERE email IN ({ph})", vals).fetchone()
    return int(row[0] or 0) if row else 0


def ledger(cx, email, limit=200):
    ph, vals = _in(identity_emails(cx, email))
    if not vals:
        return []
    rows = cx.execute(
        f"SELECT delta, reason, event_key, friend_email, inviter_slug, detail, "
        f"created_by, created_at FROM reading_credits WHERE email IN ({ph}) "
        f"ORDER BY created_at DESC, id DESC LIMIT ?", vals + (int(limit),)).fetchall()
    keys = ("delta", "reason", "event_key", "friend_email", "inviter_slug", "detail",
            "created_by", "created_at")
    return [dict(zip(keys, tuple(r))) for r in rows]


def portal_summary(cx, email):
    """What the client portal shows. None when this person has no ledger rows."""
    ph, vals = _in(identity_emails(cx, email))
    if not vals:
        return None
    row = cx.execute(f"SELECT COUNT(*), COALESCE(SUM(delta),0), "
                     f"COALESCE(SUM(CASE WHEN reason=? THEN -delta ELSE 0 END),0) "
                     f"FROM reading_credits WHERE email IN ({ph})",
                     (rules.REASON_SPEND,) + vals).fetchone()
    if not row or not int(row[0] or 0):
        return None
    bal, used = int(row[1] or 0), int(row[2] or 0)
    # earned is net of reversals, so earned - used == balance always.
    return {"balance": bal, "earned": bal + used, "used": used}


PORTAL_ANCHOR = '  part("scans", renderEyeVisionReport(d.eye_vision_report));\n'
# Client-facing copy, awaiting Glen's approval.
PORTAL_CARD_JS = '''  // Scan share reward: reading credits (injected only when SCAN_REWARD_ENABLED).
  if (d.reading_credits) {
    const rc = d.reading_credits;
    const n = Number(rc.balance) || 0;
    part("account", `<div class="card quiet" data-fold-id="account-reading-credits">
      <h2>Your free readings</h2>
      <p>You have <strong>${n}</strong> free scan reading${n === 1 ? "" : "s"} to use.</p>
      <p class="muted small">Earned ${Number(rc.earned) || 0}, used ${Number(rc.used) || 0}. You earn one when a friend you invited sends their first scan, and one for each qualifying order.</p>
    </div>`);
  }

'''


def inject_portal_card(html):
    """The portal page with the reading-credit card added. Unchanged if the anchor
    has moved, so a refactor of the page can only hide the card."""
    if PORTAL_ANCHOR not in html:
        return html
    return html.replace(PORTAL_ANCHOR, PORTAL_CARD_JS + PORTAL_ANCHOR, 1)


def spend(cx, email, *, key, by, note=""):
    """Glen or Rae marks one reading delivered for this client.

    Serialised per account and conditional in one statement, so the balance can
    never go below zero. `key` names the delivery. The same key for the same client
    is a duplicate and spends once. The same key for a different client is a
    conflict."""
    canon = canonical(cx, email)
    ids = identity_emails(cx, email)
    key = (key or "").strip()
    if not canon or not key:
        return {"ok": False, "error": "email and key required"}
    ek = f"spend:{key}"
    _lock(cx, f"reading-credits:{canon}")
    prior = cx.execute("SELECT email FROM reading_credits WHERE event_key=?", (ek,)).fetchone()
    if prior:
        cx.commit()
        if prior[0] not in ids:
            return {"ok": False, "conflict": True,
                    "error": "this key was already used for a different client"}
        return {"ok": True, "spent": False, "duplicate": True, "balance": balance(cx, canon)}
    ph, vals = _in(ids)
    cur = cx.execute(
        f"INSERT INTO reading_credits (email, delta, reason, event_key, detail, "
        f"created_by, created_at) "
        f"SELECT ?, -1, ?, ?, ?, ?, ? "
        f"WHERE (SELECT COALESCE(SUM(delta),0) FROM reading_credits "
        f"WHERE email IN ({ph})) >= 1 "
        f"ON CONFLICT(event_key) DO NOTHING",
        (canon, rules.REASON_SPEND, ek, note or "", by or "", _now()) + vals)
    wrote = (cur.rowcount or 0) > 0
    cx.commit()
    if not wrote:
        return {"ok": False, "error": "no reading credit to spend", "balance": balance(cx, canon)}
    return {"ok": True, "spent": True, "balance": balance(cx, canon)}


# ═══════════════════════════════════════════════════════════════════════════
# Piece 6: crediting
# ═══════════════════════════════════════════════════════════════════════════

def notify_inviter(inviter_email, *, reason, friend_email):
    """HOOK, deliberately empty. Telling anyone they earned a reading is out of scope
    for this change: no client email is sent. Wire send_bulk here later, after its own
    three review rounds."""
    return None


PENDING = "pending"


def _outcome(cx, event_key):
    """The final decision for an event, or None. "pending" is not a decision: it
    marks an attempt that hit a database error, for the next settle or a reconcile."""
    row = cx.execute("SELECT outcome FROM reading_credit_outcomes WHERE event_key=?",
                     (event_key,)).fetchone()
    return row[0] if row and row[0] != PENDING else None


_OUTCOME_UPSERT = ("INSERT INTO reading_credit_outcomes (event_key, outcome, detail, "
                   "decided_at) VALUES (?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET "
                   "outcome=excluded.outcome, detail=excluded.detail, "
                   "decided_at=excluded.decided_at "
                   "WHERE reading_credit_outcomes.outcome='pending'")


def mark_pending(cx, event_key, detail=""):
    """Record a retryable attempt. Never overwrites a real decision."""
    cx.execute("INSERT INTO reading_credit_outcomes (event_key, outcome, detail, decided_at) "
               "VALUES (?,?,?,?) ON CONFLICT(event_key) DO NOTHING",
               (event_key, PENDING, detail or "", _now()))
    cx.commit()


def pending_keys(cx, prefix=""):
    return [r[0] for r in cx.execute(
        "SELECT event_key FROM reading_credit_outcomes WHERE outcome=? AND event_key LIKE ? "
        "ORDER BY decided_at", (PENDING, prefix + "%")).fetchall()]


def _plan_inviter(cx, friend_ids, at, *, reason, event_key, readings, detail=""):
    """Read-only. What to do for an inviter credit."""
    done = _outcome(cx, event_key)
    if done:
        return {"action": "none", "why": f"decided:{done}"}
    holder = holder_for(cx, next(iter(sorted(friend_ids))), at)
    if not holder or not holder.get("inviter_email"):
        return {"action": "outcome", "outcome": "no-holder", "event_key": event_key}
    if friend_ids & identity_emails(cx, holder["inviter_email"]):
        return {"action": "outcome", "outcome": "self", "event_key": event_key}
    return {"action": "credit", "email": holder["inviter_email"], "delta": readings,
            "reason": reason, "event_key": event_key, "friend": canonical(cx, min(friend_ids)),
            "slug": holder["slug"], "detail": detail}


def _plan_buyer(cx, buyer_ids, *, reason, event_key, readings, detail=""):
    done = _outcome(cx, event_key)
    if done:
        return {"action": "none", "why": f"decided:{done}"}
    buyer = canonical(cx, min(buyer_ids))
    return {"action": "credit", "email": buyer, "delta": readings, "reason": reason,
            "event_key": event_key, "friend": "", "slug": "", "detail": detail}


def _system_a_owns(cx, ids):
    """True when System A's payout would find an approved affiliate for this buyer."""
    if not (_has_table(cx, "referral_events") and _has_table(cx, "affiliate_signups")):
        return False
    ph, vals = _in(ids)
    return cx.execute(
        f"SELECT 1 FROM referral_events re JOIN affiliate_signups a "
        f"ON a.slug=re.utm_source AND a.status='approved' "
        f"WHERE LOWER(re.email) IN ({ph}) LIMIT 1", vals).fetchone() is not None


def _apply(cx, plan):
    """Write a plan's rows on `cx` WITHOUT committing. The caller commits."""
    if plan["action"] == "none":
        return {"credited": False, "why": plan["why"]}
    now = _now()
    if plan["action"] == "outcome":
        cx.execute(_OUTCOME_UPSERT, (plan["event_key"], plan["outcome"], "", now))
        return {"credited": False, "why": plan["outcome"]}
    cur = cx.execute(
        "INSERT INTO reading_credits (email, delta, reason, event_key, friend_email, "
        "inviter_slug, detail, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(event_key) DO NOTHING",
        (plan["email"], int(plan["delta"]), plan["reason"], plan["event_key"],
         plan["friend"], plan["slug"], plan["detail"], "system", now))
    wrote = (cur.rowcount or 0) > 0
    cx.execute(_OUTCOME_UPSERT, (plan["event_key"], "credited", plan["email"], now))
    return {"credited": wrote, "why": "credited" if wrote else "duplicate",
            "to": plan["email"]}


def _run(cx, plan, lock_key):
    try:
        _lock(cx, lock_key)
        out = _apply(cx, plan)
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    if out.get("credited"):
        notify_inviter(plan["email"], reason=plan["reason"], friend_email=plan["friend"])
    return out


def _plan_order(cx, buyer_ids, at, *, event_key, inviter_reason, buyer_reason, detail):
    if rules.ORDER_CREDIT_TO == "buyer":
        return _plan_buyer(cx, buyer_ids, reason=buyer_reason, event_key=event_key,
                           readings=rules.READINGS_PER_QUALIFYING_ORDER, detail=detail)
    if rules.ORDER_CREDIT_TO != "inviter":
        raise ValueError(f"ORDER_CREDIT_TO must be 'buyer' or 'inviter', "
                         f"not {rules.ORDER_CREDIT_TO!r}")
    if _outcome(cx, event_key):
        return {"action": "none", "why": f"decided:{_outcome(cx, event_key)}"}
    if _system_a_owns(cx, buyer_ids):
        return {"action": "outcome", "outcome": "system-a", "event_key": event_key}
    return _plan_inviter(cx, buyer_ids, at, reason=inviter_reason, event_key=event_key,
                         readings=rules.READINGS_PER_QUALIFYING_ORDER, detail=detail)


def credit_store_order(cx, order, order_ref, *, catalog=None, is_wholesale=False,
                       below_store_price=False):
    """One reading for a paid store order with an E4L infoceutical or PRL line, to
    the buyer or the inviter per ORDER_CREDIT_TO. Idempotent per order_ref, and the
    first decision for an order is final.

    Earns nothing, as System A: a discount (discount_cents != 0), a line below its
    store price (the caller passes app._order_has_line_below_store_price), a $0
    total, or a cancelled order.

    Splitting one cart into several orders is allowed on purpose. Glen's rule is
    "one free scan per order, no minimum", so there is no per-cart or per-day cap."""
    order = order or {}
    ids = identity_emails(cx, order.get("email"))
    if not ids or not order_ref:
        return {"credited": False, "why": "no-email-or-ref"}
    if str(order.get("status") or "") == "cancelled":
        return {"credited": False, "why": "cancelled"}
    if int(order.get("discount_cents") or 0) != 0:
        return {"credited": False, "why": "discounted"}
    if below_store_price:
        return {"credited": False, "why": "below-store-price"}
    if int(order.get("total_cents") or 0) <= 0:
        return {"credited": False, "why": "zero-total"}
    at = (order.get("paid_at") or "").strip() or (order.get("created_at") or "").strip()
    if not rules.parse_ts(at):
        return {"credited": False, "why": "no-purchase-time"}
    items = order.get("items")
    if items is None:
        try:
            items = json.loads(order.get("items_json") or "[]")
        except Exception:
            items = []
    if catalog is None:
        from dashboard import products as _products
        catalog = _products._cached_products()
    if not rules.store_order_qualifies(items, catalog, is_wholesale=is_wholesale):
        return {"credited": False, "why": "not-qualifying"}
    ek = rules.event_key_store_order(order_ref)
    plan = _plan_order(cx, ids, at, event_key=ek, inviter_reason=rules.REASON_STORE_ORDER,
                       buyer_reason=rules.REASON_BUYER_STORE_ORDER, detail=order_ref)
    return _run(cx, plan, f"credit:{ek}")


def credit_e4l_direct_order(cx, buyer_email, *, message_id, ordered_at):
    """HOOK for "E4L Portal - Client's Purchase Notification" emails. Nothing parses
    those yet (verified 6 Nov 2026), so nothing calls this."""
    ek = rules.event_key_e4l_order(message_id)
    plan = _plan_order(cx, identity_emails(cx, buyer_email), ordered_at, event_key=ek,
                       inviter_reason=rules.REASON_E4L_ORDER,
                       buyer_reason=rules.REASON_BUYER_E4L_ORDER, detail=message_id)
    return _run(cx, plan, f"credit:{ek}")


def credit_prl_order(cx, buyer_email, *, order_ref, ordered_at):
    """HOOK for a PRL order under code 021a1a. No source exists yet: Glen, 6 Nov 2026,
    "We haven't had any yet." Nothing calls this."""
    ek = rules.event_key_prl_order(order_ref)
    plan = _plan_order(cx, identity_emails(cx, buyer_email), ordered_at, event_key=ek,
                       inviter_reason=rules.REASON_PRL_ORDER,
                       buyer_reason=rules.REASON_BUYER_PRL_ORDER, detail=order_ref)
    return _run(cx, plan, f"credit:{ek}")


# ── Reversals and the reconcile sweep ──────────────────────────────────────

def _order_by_ref(cx, order_ref):
    if not _has_table(cx, "orders"):
        return None
    cur = cx.execute("SELECT * FROM orders WHERE external_ref=? ORDER BY id DESC LIMIT 1",
                     (order_ref,))
    row = cur.fetchone()
    if not row:
        return None
    o = dict(zip([d[0] for d in cur.description], tuple(row)))
    try:
        o["items"] = json.loads(o.get("items_json") or "[]")
    except Exception:
        o["items"] = []
    return o


def order_is_reversed(cx, order):
    """Cancelled, or refunded in full: pay_status 'refunded', or active refunds that
    cover every active payment. A partial refund keeps the credit."""
    if str(order.get("status") or "") == "cancelled":
        return True
    if str(order.get("pay_status") or "") == "refunded":
        return True
    if order.get("id") is not None and _has_table(cx, "order_payments"):
        sums = dict(cx.execute(
            "SELECT kind, COALESCE(SUM(amount_cents),0) FROM order_payments "
            "WHERE order_id=? AND status='active' GROUP BY kind", (order["id"],)).fetchall())
        paid, refunded = int(sums.get("payment") or 0), int(sums.get("refund") or 0)
        return paid > 0 and refunded >= paid
    return False


def reverse_store_order(cx, order_ref, *, why="cancelled-or-refunded"):
    """Take back a store-order credit with a reversal row. Idempotent per order.

    Never blocked by the balance: if the reading was already spent, the reversal is
    still recorded and the balance shows negative, so Glen and Rae can see it. The
    order's outcome becomes "reversed", so no retry can credit it again."""
    ek = rules.event_key_store_order(order_ref)
    earn = cx.execute("SELECT email, delta, friend_email, inviter_slug FROM reading_credits "
                      "WHERE event_key=?", (ek,)).fetchone()
    try:
        _lock(cx, f"credit:{ek}")
        wrote = False
        if earn:
            cur = cx.execute(
                "INSERT INTO reading_credits (email, delta, reason, event_key, friend_email, "
                "inviter_slug, detail, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(event_key) DO NOTHING",
                (earn[0], -int(earn[1]), rules.REASON_REVERSAL, f"reverse:{ek}",
                 earn[2] or "", earn[3] or "", why, "system", _now()))
            wrote = (cur.rowcount or 0) > 0
        cx.execute("INSERT INTO reading_credit_outcomes (event_key, outcome, detail, "
                   "decided_at) VALUES (?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET "
                   "outcome=excluded.outcome, detail=excluded.detail",
                   (ek, "reversed", why, _now()))
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    return {"reversed": wrote}


def reconcile(cx, *, catalog=None, is_wholesale=None, below_store_price=None):
    """Finish what a settle could not. Runs from the reply-watch cron and a console
    action, flag on only.

    * Pending store-order credits (a database error at settle time) are retried.
    * Earned store-order credits whose order is now cancelled or fully refunded are
      reversed.

    is_wholesale and below_store_price are the app's own checks, passed in."""
    init_tables(cx)
    out = {"retried": 0, "credited": 0, "reversed": 0, "missing": 0}
    prefix = rules.event_key_store_order("")
    for ek in pending_keys(cx, prefix):
        ref = ek[len(prefix):]
        order = _order_by_ref(cx, ref)
        if not order:
            out["missing"] += 1
            continue
        out["retried"] += 1
        if order_is_reversed(cx, order):
            reverse_store_order(cx, ref)
            continue
        if not (str(order.get("pay_status") or "") == "paid" or str(order.get("status") or "")
                in ("paid", "shipped", "delivered", "done")):
            continue
        r = credit_store_order(
            cx, order, ref, catalog=catalog,
            is_wholesale=bool(is_wholesale(order)) if is_wholesale else False,
            below_store_price=bool(below_store_price(order)) if below_store_price else False)
        out["credited"] += int(bool(r.get("credited")))
        if not r.get("credited") and _outcome(cx, ek) is None:
            # Not creditable and not decided: close the pending row so it stops retrying.
            cx.execute(_OUTCOME_UPSERT, (ek, r.get("why") or "not-credited", "", _now()))
            cx.commit()
    for (ek,) in cx.execute(
            "SELECT event_key FROM reading_credits WHERE event_key LIKE ? AND delta>0",
            (prefix + "%",)).fetchall():
        if cx.execute("SELECT 1 FROM reading_credits WHERE event_key=?",
                      (f"reverse:{ek}",)).fetchone():
            continue
        order = _order_by_ref(cx, ek[len(prefix):])
        if order and order_is_reversed(cx, order):
            out["reversed"] += int(reverse_store_order(cx, ek[len(prefix):])["reversed"])
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Pieces 3 and 4: scan arrivals from E4L "New voice scan" notices
# ═══════════════════════════════════════════════════════════════════════════

SCAN_SENDER = "noreply@e4l.com"
SCAN_SUBJECTS = ("New voice scan", "New remote scan", "New BWS scan")
SCAN_GMAIL_QUERY = (f"from:{SCAN_SENDER} subject:("
                    + " OR ".join(f'"{s}"' for s in SCAN_SUBJECTS) + ")")
# How far back one run looks. Every notice in the window is read, oldest first.
SCAN_NOTICE_LOOKBACK_DAYS = 14
# The same pattern as 02 Skills/gmail-e4l-check.py: "...waiting for you from Ann
# Bauder (ann@example.com)". The address resolves the client without guessing by name.
_SCAN_BODY_RE = re.compile(
    r"waiting for you from\s+(.+?)\s*\(\s*([^()\s]+@[^()\s]+)\s*\)", re.IGNORECASE)
# The e4l.db-derived scan stores the app holds. History is "unknown" unless at least
# one of them exists to be asked.
SCAN_HISTORY_TABLES = (("scan_freshness", "last_scan_date"), ("client_scans", "scan_date"))


def parse_scan_notice(subject, body):
    """{"email", "client_name"} from a notice, or None when it is not one."""
    if not any(s.lower() in (subject or "").lower() for s in SCAN_SUBJECTS):
        return None
    m = _SCAN_BODY_RE.search(re.sub(r"\s+", " ", body or ""))
    if not m:
        return None
    return {"email": m.group(2).strip().lower().rstrip(".,;"),
            "client_name": m.group(1).strip()}


def scan_history(cx, ids, noticed_at, invited_at=None):
    """"first", "repeat" or "unknown" for a scan noticed at `noticed_at` (None: ask
    whether any scan at all is on record). A scan on the same day counts as prior.
    Asked under every alias. A failed query, or no history store to ask, is unknown.
    Read-only, and run before any write, so a failure cannot undo one.

    For an INVITED friend, `invited_at` is their earliest invite. The invite gate
    proved no scan was on record then, so only a synced scan dated before that day
    counts as prior. Without this, the Mac's 15-minute scan sync can write the very
    scan being noticed into client_scans first and cost the inviter the credit."""
    try:
        ph, vals = _in(ids)
        if _has_table(cx, "scan_arrivals") and cx.execute(
                f"SELECT 1 FROM scan_arrivals WHERE email IN ({ph}) LIMIT 1",
                vals).fetchone():
            return "repeat"
        asked = 0
        if invited_at:
            op, day = "<", str(invited_at)[:10]
        else:
            op, day = "<=", (noticed_at or "9999-12-31")[:10]
        for table, col in SCAN_HISTORY_TABLES:
            if not _has_table(cx, table):
                continue
            asked += 1
            if cx.execute(f"SELECT 1 FROM {table} WHERE LOWER(email) IN ({ph}) "
                          f"AND {col}{op}? LIMIT 1", vals + (day,)).fetchone():
                return "repeat"
        return "first" if asked else "unknown"
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return "unknown"


def record_scan_arrival(cx, *, msg_id, email, client_name, noticed_at):
    """Store one arrival, mark the E4L account and credit a first scan, all in ONE
    transaction. A failure rolls back everything, so the next run retries the notice.
    Returns "duplicate", "repeat", "first" or "unknown"."""
    init_tables(cx)
    has_people = ensure_people_columns(cx)
    canon = canonical(cx, email)
    ids = identity_emails(cx, email)
    if cx.execute("SELECT 1 FROM scan_arrivals WHERE gmail_msg_id=?", (msg_id,)).fetchone():
        return "duplicate"
    ph, vals = _in(ids)
    first_invite = cx.execute(f"SELECT MIN(engaged_at) FROM scan_invites "
                              f"WHERE friend_email IN ({ph})", vals).fetchone()[0]
    history = scan_history(cx, ids, noticed_at, invited_at=first_invite)
    plan = None
    if history == "first":
        plan = _plan_inviter(cx, ids, noticed_at, reason=rules.REASON_FIRST_SCAN,
                             event_key=rules.event_key_first_scan(canon),
                             readings=rules.READINGS_PER_FIRST_SCAN)
    out = None
    try:
        _lock(cx, f"scan-arrival:{canon}")
        cur = cx.execute("INSERT INTO scan_arrivals (gmail_msg_id, email, client_name, "
                         "noticed_at, history, ingested_at) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(gmail_msg_id) DO NOTHING",
                         (msg_id, canon, client_name or "", noticed_at, history, _now()))
        if (cur.rowcount or 0) == 0:
            cx.rollback()
            return "duplicate"
        if has_people:
            cx.execute("UPDATE people SET e4l_account_at=? WHERE email=? "
                       "AND (e4l_account_at IS NULL OR e4l_account_at='')",
                       (noticed_at, canon))
        if plan:
            out = _apply(cx, plan)
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    if out and out.get("credited"):
        notify_inviter(plan["email"], reason=plan["reason"], friend_email=canon)
    return history


def _notice_time(message):
    try:
        stamp = int(message.get("internalDate") or 0) / 1000
        if stamp:
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        pass
    return None


def ingest_scan_notices(cx, *, service=None, now=None):
    """Read every "New voice scan" notice in the lookback window and record each.

    Pages through all matches with Gmail's cursor before processing any, then works
    oldest first across every page, so no cap can starve an older notice. Gmail is
    read only: messages.list and messages.get. No E4L portal traffic."""
    from dashboard import inbox
    init_tables(cx)
    if service is None:
        service = inbox._get_gmail_service()
    since = int(((now or datetime.now(timezone.utc))
                 - timedelta(days=SCAN_NOTICE_LOOKBACK_DAYS)).timestamp())
    q = f"{SCAN_GMAIL_QUERY} after:{since}"
    listed, token = [], None
    while True:
        kwargs = {"userId": "me", "q": q, "maxResults": 100}
        if token:
            kwargs["pageToken"] = token
        page = service.users().messages().list(**kwargs).execute()
        listed.extend(page.get("messages") or [])
        token = page.get("nextPageToken")
        if not token:
            break

    counts = {"listed": len(listed), "first": 0, "repeat": 0, "unknown": 0,
              "duplicate": 0, "parse_failed": 0, "skipped": 0, "failed": 0}
    fetched, seen = [], set()
    for ref in listed:
        msg_id = ref.get("id")
        if not msg_id or msg_id in seen:
            counts["skipped"] += 1
            continue
        seen.add(msg_id)
        if cx.execute("SELECT 1 FROM scan_arrivals WHERE gmail_msg_id=?",
                      (msg_id,)).fetchone():
            counts["duplicate"] += 1
            continue
        message = service.users().messages().get(
            userId="me", id=msg_id, format="full").execute()
        headers = (message.get("payload") or {}).get("headers") or []
        when = _notice_time(message)
        if SCAN_SENDER not in inbox._header(headers, "From").lower() or not when:
            counts["skipped"] += 1
            continue
        fetched.append((when, msg_id, message, headers))
    fetched.sort(key=lambda f: (f[0], f[1]))
    for noticed_at, msg_id, message, headers in fetched:
        body = inbox._extract_body(message.get("payload") or {})["plain"]
        parsed = parse_scan_notice(inbox._header(headers, "Subject"), body)
        if not parsed:
            counts["parse_failed"] += 1
            continue
        try:
            outcome = record_scan_arrival(cx, msg_id=msg_id, email=parsed["email"],
                                          client_name=parsed["client_name"],
                                          noticed_at=noticed_at)
        except Exception as e:  # noqa: BLE001 - rolled back; the next run retries it
            print(f"[scan-reward] arrival {msg_id} failed, will retry: {e!r}", flush=True)
            counts["failed"] += 1
            continue
        counts[outcome] += 1
    return counts


# ═══════════════════════════════════════════════════════════════════════════
# Piece 4, one-time fill: E4L client ids from e4l.db (a production write)
# ═══════════════════════════════════════════════════════════════════════════

def _clean_id(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "none", "null") else s


def fill_e4l_client_ids(cx, rows, *, apply=False):
    """rows: [{"email", "client_id"}]. Sets people.e4l_client_id where it is empty.

    Only `apply is True` writes. A dry run never alters the table and never commits.
    Refuses an ambiguous person: one whose addresses (after alias resolution) carry
    two different ids in the source. Never overwrites, never creates a person."""
    apply = apply is True
    if not _has_table(cx, "people"):
        return {"ok": False, "error": "no people table"}
    out = {"rows": 0, "matched": 0, "already_set": 0, "would_set": 0, "set": 0,
           "unmatched": 0, "bad": 0, "ambiguous": 0, "applied": apply}
    by_person = {}
    for r in rows or []:
        out["rows"] += 1
        email = _norm((r or {}).get("email"))
        cid = _clean_id((r or {}).get("client_id"))
        if not email or "@" not in email or not cid:
            out["bad"] += 1
            continue
        by_person.setdefault(canonical(cx, email), set()).add(cid)
    if apply:
        ensure_people_columns(cx)
    has_col = _has_column(cx, "people", "e4l_client_id")
    now = _now()
    for person, cids in sorted(by_person.items()):
        if len(cids) > 1:
            out["ambiguous"] += 1
            continue
        cid = next(iter(cids))
        row = cx.execute(
            "SELECT e4l_client_id FROM people WHERE email=?" if has_col
            else "SELECT NULL FROM people WHERE email=?", (person,)).fetchone()
        if not row:
            out["unmatched"] += 1
            continue
        out["matched"] += 1
        if _clean_id(row[0]):
            out["already_set"] += 1
            continue
        out["would_set"] += 1
        if apply:
            cur = cx.execute(
                "UPDATE people SET e4l_client_id=?, "
                "e4l_account_at=COALESCE(NULLIF(e4l_account_at,''), ?) "
                "WHERE email=? AND (e4l_client_id IS NULL OR e4l_client_id='' "
                "OR LOWER(e4l_client_id) IN ('none','null'))",
                (cid, now, person))
            out["set"] += int((cur.rowcount or 0) > 0)
    if apply:
        cx.commit()
    return out
