"""New-member onboarding: a free 15-minute phone welcome call with Rae.

Reuses the EVOX booking engine (session_type='onboarding'). This module holds
only the onboarding config and the once-per-member lookup; the membership gate
(_is_paid_member) lives in the route layer so this module stays free of
app-layer imports (same shape as dashboard/consult.py)."""

import hashlib
import random

ONBOARDING = {
    "session_type": "onboarding",
    "practitioner": "rae",
    "medium": "phone",
    "duration_min": 15,
}


def daily_slot_sample(slots, limit=3, seed=None):
    """Return at most ``limit`` random available times for each calendar day.

    With a ``seed`` (the client's email) the choice holds steady for that client and day, so
    a reload does not reshuffle the times, while different clients still see different ones
    (Glen, 2026-09-28)."""
    by_day = {}
    for slot in slots:
        by_day.setdefault(slot[:10], []).append(slot)

    selected = []
    for day, day_slots in by_day.items():
        if len(day_slots) > limit:
            if seed:
                # Each time gets its own rank for this client, so booking or losing one time
                # leaves the others shown in place (review round 2).
                rank = lambda s: hashlib.sha256(f"{seed}|{s}".encode()).hexdigest()
                day_slots = sorted(day_slots, key=rank)[:limit]
            else:
                day_slots = random.sample(day_slots, limit)
        selected.extend(sorted(day_slots))
    return selected


def existing_onboarding(cx, email):
    """Return the member's currently booked onboarding row as a dict, or None.

    Booked-only (ignores cancelled rows) and onboarding-only. Used to enforce
    the once-per-member rule and to show the confirmed time on the portal card."""
    email = (email or "").strip().lower()
    row = cx.execute(
        "SELECT * FROM evox_bookings WHERE lower(email)=? "
        "AND session_type='onboarding' AND status='booked' "
        "ORDER BY start_ts DESC LIMIT 1", (email,)).fetchone()
    return dict(row) if row else None
