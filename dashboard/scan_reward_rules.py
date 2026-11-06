"""The scan share reward: every rule that decides who earns a reading, in one place.

Money signs off any change here. Everything that decides an outcome is a constant or
a pure function below. The storage and the plumbing live in dashboard/scan_reward.py
and carry no rules of their own.

Glen's rulings this encodes (plan: platform/plans/2026-11-06-scan-share-reward.md):

* One credit is "a free reading by us", published to the portal (6 Nov 2026).
* The inviter earns one reading when a friend's FIRST EVER scan arrives.
* The inviter earns one reading per qualifying order by the friend: store orders of
  E4L infoceuticals or PRL products, direct E4L orders, and PRL orders under our
  account (6 Nov 2026).
* Who owns a friend follows decision 2 of 19 Oct 2026: "the first inviter gets the
  credit exclusively for 90 days after their first engagement and 90 days after their
  last purchase."
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# ── The switch ──────────────────────────────────────────────────────────────
FLAG_ENV = "SCAN_REWARD_ENABLED"

# ── Decision 2, 19 Oct 2026: how long the first inviter holds a friend ───────
HOLD_DAYS_AFTER_FIRST_ENGAGEMENT = 90
HOLD_DAYS_AFTER_LAST_PURCHASE = 90

# Whether a first inviter whose hold has fully lapsed, with no new inviter since,
# still earns. PENDING GLEN. Default no.
LAPSED_INVITER_KEEPS_EARNING = False

# ── Who an order credit goes to ─────────────────────────────────────────────
# PENDING GLEN: does a qualifying order earn the BUYER a reading, or the friend's
# INVITER? Both are implemented. "buyer" needs no invite. "inviter" follows the hold
# and steps aside when System A (affiliate attribution) already owns the buyer, so one
# order never pays two different people a referral reward.
ORDER_CREDIT_TO = "buyer"          # "buyer" | "inviter"

# ── What each trigger earns, in readings ────────────────────────────────────
READINGS_PER_FIRST_SCAN = 1
READINGS_PER_QUALIFYING_ORDER = 1     # one per order, however many qualifying lines

# ── Which store orders qualify ──────────────────────────────────────────────
# A store line qualifies when its product is an E4L infoceutical (the catalogue's own
# test: "infoceutical" in the name, Pinecone title or URL, as sales_copy uses) or a
# PRL product. The store catalogue holds no PRL product today (checked 6 Nov 2026:
# 0 of 1,097), so the PRL slug set is empty until one is listed.
INFOCEUTICAL_MARKER = "infoceutical"
PRL_STORE_SLUGS: frozenset = frozenset()
# A practitioner drop-ship or dispensary sale earns nothing, as in System A: the
# practitioner's margin is their pay.
WHOLESALE_EARNS = False

# ── Ledger vocabulary ───────────────────────────────────────────────────────
REASON_FIRST_SCAN = "friend_first_scan"
REASON_STORE_ORDER = "friend_store_order"
REASON_E4L_ORDER = "friend_e4l_order"
REASON_PRL_ORDER = "friend_prl_order"
REASON_SPEND = "reading_delivered"
REASON_BUYER_STORE_ORDER = "own_store_order"
REASON_BUYER_E4L_ORDER = "own_e4l_order"
REASON_BUYER_PRL_ORDER = "own_prl_order"

# The source written on a scan_invites row. Invites live in their own table, never in
# referral_events, so System A's payout (_referrer_slug_for_email) cannot see them.
INVITE_SOURCE = "begin-scan"


def event_key_first_scan(friend_email):
    """One first-scan credit per friend, ever. Not per period."""
    return f"first-scan:{(friend_email or '').strip().lower()}"


def event_key_store_order(order_ref):
    return f"store-order:{(order_ref or '').strip()}"


def event_key_e4l_order(message_id):
    return f"e4l-order:{(message_id or '').strip()}"


def event_key_prl_order(order_ref):
    return f"prl-order:{(order_ref or '').strip()}"


# ── Time helpers ────────────────────────────────────────────────────────────

def parse_ts(value):
    """An ISO timestamp or date as an aware UTC datetime, or None."""
    s = str(value or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


# ── Decision 2: who holds a friend at a moment ──────────────────────────────

def _ts(v):
    return v if isinstance(v, datetime) else parse_ts(v)


def hold_until(engaged_at, purchases, before):
    """The end of a hold that began at `engaged_at`: the LATER of 90 days after that
    first engagement and 90 days after the friend's last purchase before `before`.
    Every purchase counts, including one made after an earlier lapse."""
    until = engaged_at + timedelta(days=HOLD_DAYS_AFTER_FIRST_ENGAGEMENT)
    last = max((p for p in purchases if p < before), default=None)
    if last is not None:
        until = max(until, last + timedelta(days=HOLD_DAYS_AFTER_LAST_PURCHASE))
    return until


def holder_at(engagements, purchases, at):
    """The inviter holding a friend at time `at`, or None.

    engagements: [(slug, when)] for every scan-share invite of this friend.
    purchases:   [when] for every paid order by this friend.
    at:          the moment being asked about (a datetime or ISO string).

    * The first engagement makes that inviter the holder.
    * A later engagement takes over only when BOTH windows have passed at that
      moment: 90 days from the holder's first engagement and 90 days from the
      friend's last purchase. Re-engaging inside the hold restarts nothing.
    * Only purchases strictly before `at` count, so the order being credited never
      extends its own hold.
    * Past both windows with no takeover, the first inviter earns only if
      LAPSED_INVITER_KEEPS_EARNING.

    Returns {"slug", "engaged_at", "hold_until"} or None.
    """
    at = _ts(at)
    if at is None:
        return None
    purch = sorted(t for t in (_ts(p) for p in (purchases or ())) if t is not None and t < at)
    eng = sorted((t, (slug or "").strip()) for slug, t in
                 ((s, _ts(w)) for s, w in (engagements or ()))
                 if t is not None and t <= at and (slug or "").strip())
    holder = None
    for t, slug in eng:
        if holder is None or t > hold_until(holder["engaged_at"], purch, t):
            holder = {"slug": slug, "engaged_at": t}
    if holder is None:
        return None
    holder["hold_until"] = hold_until(holder["engaged_at"], purch, at)
    if at > holder["hold_until"] and not LAPSED_INVITER_KEEPS_EARNING:
        return None
    return holder


# ── Which orders qualify ────────────────────────────────────────────────────

def line_qualifies(line, catalog):
    """True when a store order line is an E4L infoceutical or a PRL product."""
    slug = str((line or {}).get("slug") or "").strip()
    if slug in PRL_STORE_SLUGS:
        return True
    product = (catalog or {}).get(slug) or {}
    identity = " ".join(str(x or "") for x in (
        product.get("name"), product.get("pinecone_title"), product.get("url"),
        (line or {}).get("name")))
    return INFOCEUTICAL_MARKER in identity.lower()


def store_order_qualifies(items, catalog, *, is_wholesale=False):
    """True when a paid store order earns the inviter a reading."""
    if is_wholesale and not WHOLESALE_EARNS:
        return False
    return any(line_qualifies(it, catalog) for it in (items or ()) if _qty(it) > 0)


def _qty(line):
    """A line's quantity. Missing means 1. Zero or junk never qualifies."""
    q = (line or {}).get("qty")
    if q is None:
        return 1
    try:
        return int(q)
    except (TypeError, ValueError):
        return 0
