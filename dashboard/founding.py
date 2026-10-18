"""Founding-launch config + counter. Pure module: the only I/O is reading the
JSON config at import and counting rows via a caller-supplied sqlite connection."""

import json
import logging
import os

from dashboard import subscriptions as _subs

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "data", "founding_launches.json")


def _load():
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("founding: could not load %s: %s", _CONFIG_PATH, e)
        return {}


_CONFIG = _load()


def get_launch(slug: str) -> dict | None:
    return _CONFIG.get(slug)


def count_reserved(cx, slug: str) -> int:
    return _subs.count_founding(cx, slug)


def remaining(cx, slug: str) -> int:
    launch = get_launch(slug)
    if not launch:
        return 0
    return max(0, int(launch.get("cap", 0)) - count_reserved(cx, slug))


def is_open(cx, slug: str, *, now_iso: str | None = None) -> bool:
    launch = get_launch(slug)
    if not launch:
        return False
    if remaining(cx, slug) <= 0:
        return False
    closes_at = (launch.get("closes_at") or "").strip()
    if closes_at and now_iso and now_iso >= closes_at:
        return False
    return True


# ── Founding membership: from shipment to 30 days after delivery ──────────────
# Glen, 2026-10-14: "start at shipment", end "30 days after delivery". The ship
# charge records its order here; the first delivery signal for that order sets
# the end date. One row per shipped founding order, so a second delivery signal
# for the same parcel cannot move the end date.

MEMBERSHIP_DAYS_AFTER_DELIVERY = 30


def init_ship_memberships(cx) -> None:
    cx.execute("CREATE TABLE IF NOT EXISTS founding_ship_memberships "
               "(order_ref TEXT PRIMARY KEY, sub_id INTEGER, email TEXT, "
               " shipped_at TEXT, delivered_at TEXT)")


def record_ship(cx, *, order_ref: str, sub_id: int, email: str, shipped_at: str) -> bool:
    """Remember which order carried a founding bottle. True only the first time
    for that order. Does not commit."""
    init_ship_memberships(cx)
    cur = cx.execute("INSERT INTO founding_ship_memberships (order_ref, sub_id, email, shipped_at) "
               "VALUES (?,?,?,?) ON CONFLICT(order_ref) DO NOTHING",
               (order_ref, sub_id, email, shipped_at))
    return bool(cur.rowcount)


def claim_delivery(cx, order_ref: str, delivered_at: str) -> str | None:
    """Mark a founding order delivered. Returns the founder's email the first
    time only, and None for any other order or a repeat. Does not commit."""
    if not order_ref:
        return None
    init_ship_memberships(cx)
    cur = cx.execute("UPDATE founding_ship_memberships SET delivered_at=? "
                     "WHERE order_ref=? AND delivered_at IS NULL", (delivered_at, order_ref))
    if not cur.rowcount:
        return None
    row = cx.execute("SELECT email FROM founding_ship_memberships WHERE order_ref=?",
                     (order_ref,)).fetchone()
    return row[0] if row else None


def claim_delivery_by_email(cx, email: str, delivered_at: str) -> str | None:
    """Mark a founder's oldest undelivered shipped bottle delivered, for a delivery
    seen on the reservation's own order card. Same contract as claim_delivery."""
    email = (email or "").strip().lower()
    if not email:
        return None
    init_ship_memberships(cx)
    row = cx.execute("SELECT order_ref FROM founding_ship_memberships "
                     "WHERE email=? AND delivered_at IS NULL ORDER BY shipped_at LIMIT 1",
                     (email,)).fetchone()
    return claim_delivery(cx, row[0], delivered_at) if row else None
