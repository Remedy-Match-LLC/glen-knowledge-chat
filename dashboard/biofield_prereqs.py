"""Is a client ready to pay for a Biofield Analysis?

Glen, 2026-10-02: every Biofield client completes onboarding before paying, because
otherwise "she pays and 'waits'". Onboarding is a fresh Bioenergetic Wellness Scan, the
intake and a recent photo.

The portal and the readiness page used to read different records for the same three
steps, so a photo uploaded in the portal stayed red on the readiness page. This module
is the one answer both use, and the checkout uses it too. Each step is satisfied by any
of the records that can show it. Never raises: an unreadable record counts as not done.
"""
from datetime import date, timedelta

from dashboard import biofield_store, client_photos, client_scans, intake, scan_freshness

SCAN_WINDOW_DAYS = 7
INTAKE_LEAD_SOURCES = ("scoreapp", "practice-better", "concierge")


def _safe(fn):
    try:
        return bool(fn())
    except Exception:
        return False


def _norm(email):
    return (email or "").strip().lower()


def has_photo(cx, email):
    e = _norm(email)
    if not e:
        return False
    row = _safe(lambda: (biofield_store.get(cx, e) or {}).get("photo_on_file"))
    return row or _safe(lambda: client_photos.has(cx, e))


def has_intake(cx, email):
    e = _norm(email)
    if not e:
        return False
    if _safe(lambda: (biofield_store.get(cx, e) or {}).get("intake_confirmed")):
        return True
    if _safe(lambda: intake.is_submitted(cx, e)):
        return True
    marks = ",".join("?" * len(INTAKE_LEAD_SOURCES))
    return _safe(lambda: cx.execute(
        f"SELECT 1 FROM inbound_leads WHERE lower(email)=? AND source IN ({marks}) LIMIT 1",
        (e, *INTAKE_LEAD_SOURCES)).fetchone())


def _within_window(scan_date, today):
    try:
        d = date.fromisoformat(str(scan_date)[:10])
    except (TypeError, ValueError):
        return False
    return today - timedelta(days=SCAN_WINDOW_DAYS) <= d <= today


def has_fresh_scan(cx, email, *, today):
    e = _norm(email)
    if not e:
        return False
    if isinstance(today, str):
        today = date.fromisoformat(today[:10])
    if _safe(lambda: (biofield_store.get(cx, e) or {}).get("scan_confirmed")):
        return True
    if _safe(lambda: scan_freshness.is_fresh(cx, e, today=today.isoformat(),
                                             window_days=SCAN_WINDOW_DAYS)):
        return True
    return _safe(lambda: any(_within_window(s["scan_date"], today)
                             for s in client_scans.scans_for(cx, e)))


def status(cx, email, *, today):
    photo = has_photo(cx, email)
    intake_done = has_intake(cx, email)
    scan = has_fresh_scan(cx, email, today=today)
    return {"photo": photo, "intake": intake_done, "scan": scan,
            "ready": bool(photo and intake_done and scan)}
