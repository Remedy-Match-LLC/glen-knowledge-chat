"""Cadence click tracking: which contact clicked which week's email, and where to.

Glen approved an intrigue-then-interest cadence on 2026-09-18: only people who click an
Intrigue email get the Interest (offer) emails that week. The spec is section 5 of
marketing/03 Marketing/cadence-rotation-plan-2026-q4.html.

GET /c/<token>/<campaign_key>/<dest_key> records a click and redirects. The target is
ALWAYS looked up in DESTINATIONS below (or a validated product slug) and never built
from the request, so the route cannot be used as an open redirect.
"""
import re
from datetime import datetime, timezone

# dest_key -> where the link lands. Adding a key here is the only way to add a target.
DESTINATIONS = {
    "scan": "/begin/scan",
    "quiz": "/begin/quiz",
    "patterns": "/learn/patterns",
    "glossary": "/learn/glossary",
    "explore": "/begin/explore",
    "practitioner": "/practitioner",
    "eye-programs": "/practitioner/eye-programs",
    "membership": "https://myhealingoasis.com/membership",
}
PRODUCT_PREFIX = "p-"

# One campaign per ISO week: 2026-w41.
CAMPAIGN_RE = re.compile(r"^\d{4}-w\d{2}$")
# A plain mailbox check. It rejects junk, not every RFC oddity.
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_TOKEN_BATCH = 1000


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_cadence_clicks(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS cadence_clicks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, "
        "campaign_key TEXT NOT NULL, dest_key TEXT NOT NULL, "
        "clicked_at TEXT NOT NULL, user_agent TEXT)"
    )
    cx.execute("CREATE INDEX IF NOT EXISTS ix_cadence_clicks_campaign "
               "ON cadence_clicks(campaign_key, email)")
    cx.commit()


def valid_campaign(campaign_key):
    return bool(CAMPAIGN_RE.match(campaign_key or ""))


def valid_email(email):
    return bool(EMAIL_RE.match((email or "").strip()))


def resolve(dest_key, product_slug_check):
    """dest_key -> (target, normalized key), or (None, None) for anything unknown.

    product_slug_check(slug) returns the catalog slug or None; only a slug it accepts
    becomes a product page."""
    key = (dest_key or "").strip().lower()
    if key in DESTINATIONS:
        return DESTINATIONS[key], key
    if key.startswith(PRODUCT_PREFIX):
        slug = product_slug_check(key[len(PRODUCT_PREFIX):])
        if slug:
            return f"/begin/product/{slug}", PRODUCT_PREFIX + slug
    return None, None


def record(cx, email, campaign_key, dest_key, user_agent):
    cx.execute(
        "INSERT INTO cadence_clicks (email, campaign_key, dest_key, clicked_at, user_agent) "
        "VALUES (?,?,?,?,?)",
        ((email or "").strip().lower(), campaign_key, dest_key, _now(),
         (user_agent or "")[:500]))
    cx.commit()


def clickers(cx, campaign_key):
    """Everyone who clicked this campaign: email, first click time, and the destination
    of that first click. All clicks are kept; mail scanners can be picked out later by
    user agent."""
    rows = cx.execute(
        "SELECT email, clicked_at, dest_key, user_agent FROM cadence_clicks "
        "WHERE campaign_key=? ORDER BY clicked_at, id", (campaign_key,)).fetchall()
    first = {}
    for email, at, dest, ua in rows:
        if email not in first:
            first[email] = {"email": email, "first_click_at": at, "dest_key": dest,
                            "user_agent": ua or "", "clicks": 0}
        first[email]["clicks"] += 1
    return list(first.values())
