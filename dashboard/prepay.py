"""Continuous Care pricing — the single source of truth for Continuous Care terms.

Continuous Care is term-based and never auto-renews: a client buys a month, or
prepays 3/6/12 months up front at a modest progressive discount graduated to
"2 months free" at the annual term. All money is in CENTS. This is a PURE module
(no Flask, no Stripe, no DB) — term math delegates to subscriptions.add_months so
tests run in isolation, mirroring subscriptions.py.

The monthly term IS the $100/mo care rate (Glen, 2026-11-12; it was $99): MONTHLY_ANCHOR_CENTS is the single
source for that rate (also the reroute target for the monthly literals in
group_bundle.py / portal_offers.py and app.py's QBO tier).
"""

from dashboard import subscriptions as _subs

# The $100/mo rate (Glen, 2026-11-12: "$100 a month, $1,000 a year"). Single source
# of truth for new purchases. A subscription stores its own amount, so members who
# joined at $99 stay at $99.
MONTHLY_ANCHOR_CENTS = 10000

# The renewal reminder quoted a $91/mo "loyalty" rate, which was the 6-month rate.
# Nothing ever charged it: checkout charges the tier price. With the 6-month term
# discontinued (Glen, 2026-11-12), the reminder quotes the real checkout price.

# Continuous Care terms, in ascending order. price_cents is the one-time prepay
# total. Discounts are graduated (0% / ~2% / ~8% / ~17%) capping at "2 months free"
# at the annual term — a modest loyalty reward, not a fire-sale.
TIERS = [
    {"key": "1mo",  "months": 1,  "price_cents": 10000, "badge": "",             "label": "Monthly"},
    {"key": "3mo",  "months": 3,  "price_cents": 29000, "badge": "",             "label": "3 months"},
    {"key": "6mo",  "months": 6,  "price_cents": 54600, "badge": "", "label": "6 months"},
    {"key": "12mo", "months": 12, "price_cents": 100000, "badge": "2 months free", "label": "12 months"},
]

_BY_KEY = {t["key"]: t for t in TIERS}

# The public-facing picker ladder, and the only terms checkout sells. 3mo and 6mo are
# retired (6mo by Glen, 2026-11-12: "discontinue the 6 month offer"). Their TIERS
# entries stay so a term already bought still reads, grants and expires.
PUBLIC_TIER_KEYS = ["1mo", "12mo"]

# Tiers that represent a term commitment (as opposed to the no-commitment
# monthly anchor) — used by the picker to show the "pay monthly vs pay up
# front" choice.
COMMITMENT_TIER_KEYS = ["12mo"]


def get_tier(key):
    """Return the tier descriptor dict for *key*, or None if unknown."""
    return _BY_KEY.get(key) if key else None


def per_month_cents(key) -> int:
    """Effective per-month cost of a tier (rounded to the nearest cent)."""
    t = get_tier(key)
    if not t:
        return 0
    return round(t["price_cents"] / t["months"])


def savings_pct_vs_anchor(key) -> int:
    """Percent saved vs paying the monthly anchor for the same number of months
    (rounded). 0 for the 1-month anchor tier."""
    t = get_tier(key)
    if not t:
        return 0
    full = MONTHLY_ANCHOR_CENTS * t["months"]
    if full <= 0:
        return 0
    return round((full - t["price_cents"]) / full * 100)


def monthly_total_cents(key) -> int:
    """What the same number of months would cost paid monthly at the anchor
    rate (no discount) — the "pay monthly" comparison figure for a term."""
    t = get_tier(key)
    return MONTHLY_ANCHOR_CENTS * t["months"] if t else 0


def upfront_savings_cents(key) -> int:
    """How much a term saves vs paying monthly for the same span, floored at 0."""
    t = get_tier(key)
    return max(0, monthly_total_cents(key) - t["price_cents"]) if t else 0


def tiers_public() -> list:
    """The ladder as the picker UI needs it: descriptor + computed per-month +
    savings %. No hidden fields, safe to serialize to the page. Only the
    public tiers (3mo retired — see PUBLIC_TIER_KEYS) are included."""
    return [
        {
            "key": t["key"],
            "months": t["months"],
            "price_cents": t["price_cents"],
            "per_month_cents": per_month_cents(t["key"]),
            "savings_pct": savings_pct_vs_anchor(t["key"]),
            "badge": t["badge"],
            "label": t["label"],
            "commitment": t["key"] in COMMITMENT_TIER_KEYS,
            "monthly_cents": MONTHLY_ANCHOR_CENTS,
            "monthly_total_cents": monthly_total_cents(t["key"]),
            "upfront_savings_cents": upfront_savings_cents(t["key"]),
        }
        for t in TIERS
        if t["key"] in PUBLIC_TIER_KEYS
    ]


def term_end_date(start_yyyy_mm_dd: str, months: int) -> str:
    """Calendar-accurate term-end date (delegates to subscriptions.add_months, so
    month-end clamping matches the rest of the subscription code)."""
    return _subs.add_months(start_yyyy_mm_dd, int(months))


def term_days(start_yyyy_mm_dd: str, months: int) -> int:
    """Number of days in an N-month term starting on *start* — the grant length
    to pass to _grant_membership. Calendar-accurate (leap years, month lengths)."""
    from datetime import datetime
    start = datetime.strptime(start_yyyy_mm_dd, "%Y-%m-%d")
    end = datetime.strptime(term_end_date(start_yyyy_mm_dd, months), "%Y-%m-%d")
    return (end - start).days


def renewal_price_cents(key):
    """What checkout charges to renew the SAME term, or None when that term is not
    sold any more (3mo, 6mo) or is unknown. Used only for the renewal-prompt copy;
    no charge is ever made automatically."""
    if key not in PUBLIC_TIER_KEYS:
        return None
    t = get_tier(key)
    return t["price_cents"] if t else None


def is_sold(key):
    """True for a term checkout still sells."""
    return key in PUBLIC_TIER_KEYS and get_tier(key) is not None
