"""Membership product catalog: the three buyable tiers, all granting the same
entitlement (live group coaching + member pricing) and differing only in billing.
Reuses existing fulfillment primitives — one-time tiers are grant-only (like the
prepay ladder, so the charge cron never bills them and they cannot auto-renew);
the recurring-capped tier uses a subscriptions row with term_charges_total that
self-cancels at the cap (dashboard.subscriptions + app charge cron)."""
import calendar
import datetime
import os

GRACE_DAYS = 4

TIERS = {
    "month": {
        "key": "month", "label": "Monthly Membership", "price_cents": 9900,
        "billing": "one_time", "source": "membership_month",
        "term_charges": 1, "cadence_months": 1, "grant_months": 1,
    },
    "year_monthly": {
        "key": "year_monthly", "label": "Annual Membership (monthly)",
        "price_cents": 9900, "billing": "recurring_capped",
        "source": "membership_year_monthly",
        "term_charges": 12, "cadence_months": 1, "grant_months": 12,
    },
    "year_prepay": {
        "key": "year_prepay", "label": "Annual Membership (full pay)",
        "price_cents": 99000, "billing": "one_time",
        "source": "membership_year_prepay",
        "term_charges": 1, "cadence_months": 1, "grant_months": 12,
    },
}

_ORDER = ["month", "year_monthly", "year_prepay"]

def get_tier(key):
    return TIERS.get(key)

def all_tiers():
    return [TIERS[k] for k in _ORDER]

def _add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)

def grant_days(key, today):
    t = TIERS[key]
    end = _add_months(today, t["grant_months"])
    return (end - today).days + GRACE_DAYS

def _tier_sources():
    return tuple(t["source"] for t in TIERS.values())

# Glen's member-for-life grant (POST /admin/membership/grant, lifetime=true). It is
# stored with expires_at NULL, which `expires_at > now` never matches, so a lifetime
# member was offered the live group they already own (money, 2026-09-24).
LIFETIME_SOURCE = "owner_lifetime"

# The membership that comes with the ASH certification includes the live group
# (Glen, 2026-09-25, relayed by money). Dated, so it owns only while unexpired.
# Cash, video, studio_credit and the other bonus_* grants were not ruled on and stay out.
GROUP_BONUS_SOURCES = ("bonus_cert",)

# "Unexpired" for a memberships row: a future expiry, or a lifetime grant with none.
# ONLY owner_lifetime: a NULL biofield_trial row (the $1 unlock, lifetime by design)
# must not become a member. One bind parameter: now.
ACTIVE_GRANT_SQL = (f"(expires_at > ? OR (expires_at IS NULL AND source = '{LIFETIME_SOURCE}'))")


def owns_group(cx, email, *, include_bonus=True):
    """True iff the email holds an active membership-tier grant, Glen's lifetime
    grant, or an active ASH-certification membership (bonus_cert). Namespaced to those sources so prepay/continuous-care/founding grants are
    unaffected."""
    if not email:
        return False
    now = datetime.datetime.utcnow().isoformat()
    # include_bonus=False is for the paid-order grant hooks: a dated bonus grant is not a
    # paid membership, so paying for one must still write a grant, and a Biofield buyer
    # keeps the care-taster month (review round 1; Glen ruled on the group only).
    srcs = _tier_sources() + (LIFETIME_SOURCE,) + (GROUP_BONUS_SOURCES if include_bonus else ())
    ph = ",".join("?" * len(srcs))
    row = cx.execute(
        f"SELECT 1 FROM memberships WHERE lower(email)=lower(?) "
        f"AND {ACTIVE_GRANT_SQL} AND source IN ({ph}) LIMIT 1",
        (email, now, *srcs)).fetchone()
    return row is not None


_MEMBERSHIP_LINE_PREFIX = "membership:"


def line_slug(tier_key):
    return f"{_MEMBERSHIP_LINE_PREFIX}{tier_key}"


def line_for(tier_key):
    """The stored order-line dict for a membership tier, or None if the tier is unknown.
    Carries kind='membership' + tier so pricing/rendering can recognize it without a
    product-catalog lookup (the slug is intentionally NOT a catalog product)."""
    t = TIERS.get(tier_key)
    if not t:
        return None
    return {"slug": line_slug(tier_key), "name": t["label"], "qty": 1,
            "unit_cents": t["price_cents"], "line_cents": t["price_cents"],
            "kind": "membership", "tier": tier_key}


def tier_of_line(line):
    """Tier key if `line` is a membership line (by kind marker or slug prefix), else None."""
    if not isinstance(line, dict):
        return None
    if line.get("kind") == "membership":
        tk = line.get("tier") or (line.get("slug") or "")[len(_MEMBERSHIP_LINE_PREFIX):]
        return tk if tk in TIERS else None
    slug = (line.get("slug") or "")
    if slug.startswith(_MEMBERSHIP_LINE_PREFIX):
        tk = slug[len(_MEMBERSHIP_LINE_PREFIX):]
        return tk if tk in TIERS else None
    return None


def cart_has_membership_tier(lines):
    """First membership tier key present in `lines`, else None."""
    for ln in (lines or []):
        tk = tier_of_line(ln)
        if tk:
            return tk
    return None


def invoice_offer_tiers():
    """Tier keys offered by the on-invoice membership control. Configurable via the
    MEMBERSHIP_INVOICE_TIERS env var (comma-separated); unknown tiers are dropped;
    default ['month']."""
    raw = (os.environ.get("MEMBERSHIP_INVOICE_TIERS") or "").strip()
    if not raw:
        return ["month"]
    out = [k.strip() for k in raw.split(",") if k.strip() in TIERS]
    return out or ["month"]
