"""Each client's time zone, and a booked time described in it with Hawaii time in brackets.

Glen, 2026-09-28: store each client's zone (the browser's on first visit, changeable in the
portal) and show every time in it, with Hawaii time in brackets, because Rae and Glen work in
Hawaii and "HST" means nothing to most clients. Booking times are stored as naive Hawaii
wall-clock strings ("2026-09-30T11:00:00"), which is what `describe` reads.
"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HAWAII = "Pacific/Honolulu"


def valid_zone(tz):
    """The zone name if it is a real IANA zone, else ''."""
    tz = (tz or "").strip()
    if not tz or len(tz) > 64:
        return ""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ""
    return tz


def _init(cx):
    """Add the column once. A SELECT first, so the common case takes no table lock."""
    try:
        cx.execute("SELECT time_zone FROM people LIMIT 0")
        return
    except Exception:  # noqa: BLE001 - column missing
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
    try:
        cx.execute("ALTER TABLE people ADD COLUMN time_zone TEXT DEFAULT ''")
    except Exception:  # noqa: BLE001 - another worker added it
        try:
            cx.rollback()       # leave no aborted transaction behind (review round 3)
        except Exception:  # noqa: BLE001
            pass


def _person_email(cx, email):
    """The surviving person's address: a merged-away address saves onto the survivor."""
    email = (email or "").strip().lower()
    try:
        from dashboard import person_aliases as _pa
        return _pa.canonical_email(cx, email) or email
    except Exception:  # noqa: BLE001 - no alias table yet
        return email


def get_zone(cx, email):
    """The client's stored zone, or '' when none is stored."""
    _init(cx)
    row = cx.execute("SELECT time_zone FROM people WHERE lower(email)=? LIMIT 1",
                     (_person_email(cx, email),)).fetchone()
    return valid_zone(row[0] if row else "")


def set_zone(cx, email, tz, source="chosen"):
    """Store a zone on the person's own record and return what is stored ('' when there is no
    such person: this never creates one). A zone the browser reported only fills an empty
    value, in the same statement, so a choice saved by another worker is never replaced.
    Caller commits."""
    tz = valid_zone(tz)
    if not tz:
        raise ValueError("unknown time zone")
    _init(cx)
    who = _person_email(cx, email)
    if not who:
        return ""
    if source == "browser":
        cx.execute("UPDATE people SET time_zone=? WHERE lower(email)=? "
                   "AND COALESCE(time_zone, '')=''", (tz, who))
    else:
        cx.execute("UPDATE people SET time_zone=? WHERE lower(email)=?", (tz, who))
    return get_zone(cx, who)


def _clock(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def _day(dt):
    return f"{dt:%a} {dt.day} {dt:%b}"


def local_only(hawaii_ts, tz):
    """'Wed 30 Sep, 2:00 pm' in the client's zone, with no Hawaii bracket (for staff emails)."""
    zone = valid_zone(tz) or HAWAII
    there = (datetime.fromisoformat(str(hawaii_ts)[:19]).replace(tzinfo=ZoneInfo(HAWAII))
             .astimezone(ZoneInfo(zone)))
    return f"{_day(there)}, {_clock(there)}"


def describe(hawaii_ts, tz):
    """'Wed 30 Sep, 2:00 pm (11:00 am Hawaii)' in the client's zone; 'Wed 30 Sep, 11:00 am
    Hawaii time' for a Hawaii client or when no usable zone is stored."""
    here = datetime.fromisoformat(str(hawaii_ts)[:19]).replace(tzinfo=ZoneInfo(HAWAII))
    zone = valid_zone(tz)
    if not zone or zone == HAWAII:
        return f"{_day(here)}, {_clock(here)} Hawaii time"
    there = here.astimezone(ZoneInfo(zone))
    if there.utcoffset() == here.utcoffset():
        return f"{_day(here)}, {_clock(here)} Hawaii time"
    hawaii = _clock(here) if there.date() == here.date() else f"{_clock(here)} {_day(here)}"
    return f"{_day(there)}, {_clock(there)} ({hawaii} Hawaii)"
