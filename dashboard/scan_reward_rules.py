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

# The referral_events medium written when a friend gives an email on /begin/scan.
REFERRAL_MEDIUM = "scan-share"
REFERRAL_CAMPAIGN = "begin-scan"


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

def holder_at(engagements, purchases, at):
    """The inviter holding a friend at time `at`, or None.

    engagements: [(slug, when)] for every approved-affiliate referral of this friend.
    purchases:   [when] for every paid order by this friend.
    at:          the moment being asked about (a datetime or ISO string).

    The rule, walked in time order:
    * The first engagement makes that inviter the holder. The hold runs 90 days.
    * A purchase while held extends the hold to 90 days after that purchase.
    * A different inviter's engagement while the hold stands changes nothing.
      The same inviter re-engaging does not restart the clock either: the rule says
      "after their FIRST engagement".
    * Once the hold has lapsed, the next engagement starts a new hold for whoever
      made it.

    Returns {"slug", "engaged_at", "hold_until"} or None.
    """
    at = parse_ts(at) if not isinstance(at, datetime) else at
    if at is None:
        return None
    events = []
    for slug, when in engagements or ():
        t = parse_ts(when) if not isinstance(when, datetime) else when
        slug = (slug or "").strip()
        if t is not None and slug and t <= at:
            events.append((t, 0, slug))
    for when in purchases or ():
        t = parse_ts(when) if not isinstance(when, datetime) else when
        if t is not None and t <= at:
            events.append((t, 1, None))
    # Same instant: the engagement is applied before the purchase.
    events.sort(key=lambda e: (e[0], e[1]))

    holder = None
    for t, kind, slug in events:
        if kind == 0:
            if holder is None or t > holder["hold_until"]:
                holder = {"slug": slug, "engaged_at": t,
                          "hold_until": t + timedelta(days=HOLD_DAYS_AFTER_FIRST_ENGAGEMENT)}
        elif holder is not None and t <= holder["hold_until"]:
            holder["hold_until"] = max(
                holder["hold_until"], t + timedelta(days=HOLD_DAYS_AFTER_LAST_PURCHASE))
    if holder is None or at > holder["hold_until"]:
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
    return any(line_qualifies(it, catalog) for it in (items or ())
               if int((it or {}).get("qty") or 1) > 0)
