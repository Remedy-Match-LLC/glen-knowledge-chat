"""Is a client ready to pay for a Biofield Analysis?

Glen, 2026-10-02: every Biofield client completes onboarding before paying, because
otherwise "she pays and 'waits'". Onboarding is a fresh Bioenergetic Wellness Scan, the
intake and a recent photo.

The portal and the readiness page used to read different records for the same three
steps, so a photo uploaded in the portal stayed red on the readiness page. This module
is the one answer both use, and the checkout uses it too. Each step is satisfied by any
of the records that can show it. Never raises: an unreadable record counts as not done.
"""
from datetime import date, datetime, timedelta, timezone

from dashboard import client_photos, client_scans, intake, scan_freshness

SCAN_WINDOW_DAYS = 7
INTAKE_LEAD_SOURCES = ("scoreapp", "practice-better", "concierge")


_SP = 0


def _guard(cx, fn, default):
    """Run one source read inside a savepoint. On Postgres a failed statement aborts
    the whole transaction, so a missing table or column here would make every later
    read on the connection fail too, the portal's other blocks included. The savepoint
    rolls back only this read. Works the same on sqlite."""
    global _SP
    _SP += 1
    name = f"bfpre_{_SP}"
    try:
        cx.execute(f"SAVEPOINT {name}")
    except Exception:
        name = None
    try:
        out = fn()
        if name:
            cx.execute(f"RELEASE SAVEPOINT {name}")
        return out
    except Exception:
        if name:
            try:
                cx.execute(f"ROLLBACK TO SAVEPOINT {name}")
                cx.execute(f"RELEASE SAVEPOINT {name}")
            except Exception:
                pass
        return default


def _norm(email):
    return (email or "").strip().lower()


def _readiness(cx, email):
    """The readiness page's own record, read by column position. biofield_store.get
    needs a dict row factory, which the app's connections do not set, and a failed
    read here once looked exactly like "nothing confirmed"."""
    r = _guard(cx, lambda: cx.execute(
        "SELECT photo_on_file, intake_confirmed, scan_confirmed, scan_confirmed_at "
        "FROM biofield_readiness WHERE email=?", (email,)).fetchone(), False)
    if r is False:
        # A table not yet migrated has no scan_confirmed_at. Read the rest, so a
        # photo and intake confirmed there still count.
        r = _guard(cx, lambda: cx.execute(
            "SELECT photo_on_file, intake_confirmed, scan_confirmed, NULL "
            "FROM biofield_readiness WHERE email=?", (email,)).fetchone(), None)
    if not r:
        return {}
    return {"photo_on_file": r[0], "intake_confirmed": r[1],
            "scan_confirmed": r[2], "scan_confirmed_at": r[3]}


def is_paid(cx, email):
    """A $300 payment is on record. Read by position, behind a savepoint."""
    e = _norm(email)
    if not e:
        return False
    r = _guard(cx, lambda: cx.execute(
        "SELECT paid_at FROM biofield_readiness WHERE email=?", (e,)).fetchone(), None)
    return bool(r and r[0])


def has_photo(cx, email):
    e = _norm(email)
    if not e:
        return False
    return bool(_readiness(cx, e).get("photo_on_file")) or bool(_guard(cx, lambda: client_photos.has(cx, e), False))


def has_intake(cx, email):
    e = _norm(email)
    if not e:
        return False
    if _readiness(cx, e).get("intake_confirmed"):
        return True
    if _guard(cx, lambda: intake.is_submitted(cx, e), False):
        return True
    marks = ",".join("?" * len(INTAKE_LEAD_SOURCES))
    return bool(_guard(cx, lambda: cx.execute(
        f"SELECT 1 FROM inbound_leads WHERE lower(email)=? AND source IN ({marks}) LIMIT 1",
        (e, *INTAKE_LEAD_SOURCES)).fetchone(), None))


def business_today():
    """Today in Hawai'i, where the business and most scan dates are. A UTC date runs
    ahead from 14:00 HST and closed the window early. Stored confirmations are UTC
    timestamps; the window's one-day lead absorbs that."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Pacific/Honolulu")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=10)).date()


def _within_window(scan_date, today):
    """A date-only window. One day ahead is allowed: a scan dated in Hawai'i, a
    confirmation stamped in UTC and the server clock can sit a calendar day apart."""
    try:
        d = date.fromisoformat(str(scan_date)[:10])
    except (TypeError, ValueError):
        return False
    return today - timedelta(days=SCAN_WINDOW_DAYS) <= d <= today + timedelta(days=1)


def has_fresh_scan(cx, email, *, today):
    e = _norm(email)
    if not e:
        return False
    if isinstance(today, str):
        today = date.fromisoformat(today[:10])
    # A self-confirmation counts only while it is inside the window. One with no
    # date predates scan_confirmed_at and is not trusted for a payment.
    row = _readiness(cx, e)
    if row.get("scan_confirmed") and _within_window(row.get("scan_confirmed_at"), today):
        return True
    d = _guard(cx, lambda: scan_freshness.latest_scan_date(cx, e), None)
    if d and _within_window(d, today):
        return True
    return bool(_guard(cx, lambda: any(_within_window(s["scan_date"], today)
                                       for s in client_scans.scans_for(cx, e)), False))


def status(cx, email, *, today):
    photo = has_photo(cx, email)
    intake_done = has_intake(cx, email)
    scan = has_fresh_scan(cx, email, today=today)
    return {"photo": photo, "intake": intake_done, "scan": scan,
            "ready": bool(photo and intake_done and scan)}
