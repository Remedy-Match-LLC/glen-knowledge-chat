"""Per-practitioner booking configuration and timezone-correct availability.

Section 3a of the practitioner website spec. The booking CORE already handles
multiple practitioners: evox_bookings carries a practitioner column and the
double-booking guard is a database UNIQUE index on (practitioner, start_ts).
What was hardcoded was the callers. This module holds the data those callers
were missing.

Deliberately excluded: anything Google. Reading a practitioner's external
calendar is plan 3b. Until it lands, availability is her declared hours minus
bookings in OUR table, so a commitment that lives only in her Google Calendar
will still be offered. Her config page says so in as many words.
"""
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dashboard import db

DEFAULT_TIMEZONE = "Pacific/Honolulu"
MEDIA = ("phone", "zoom", "in-person")
MAX_LABEL = 80
MAX_SESSION_TYPES = 8
MIN_DURATION, MAX_DURATION = 5, 600

# "<day>-<day>:<HH:MM>-<HH:MM>", the format dashboard.evox.parse_office_hours
# already understands. Kept identical so the grid needs no new parser.
_HOURS_RE = re.compile(r"^([1-7])-([1-7]):([0-2]\d):([0-5]\d)-([0-2]\d):([0-5]\d)$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TAG_RE = re.compile(r"<[^>]*>")


class BookingConfigError(ValueError):
    """A config that must not reach the slot grid."""


def init_tables(cx) -> None:
    cx.execute("""CREATE TABLE IF NOT EXISTS practitioner_booking_config (
        practitioner_id TEXT PRIMARY KEY,
        timezone TEXT NOT NULL,
        office_hours TEXT NOT NULL,
        session_types TEXT NOT NULL,
        notice_hours INTEGER NOT NULL DEFAULT 24,
        buffer_min INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT)""")
    cx.commit()


def _text(v, limit):
    return _TAG_RE.sub("", str(v or "")).strip()[:limit]


def _validate_hours(spec):
    m = _HOURS_RE.match(str(spec or "").strip())
    if not m:
        raise BookingConfigError(
            "Office hours must look like 1-5:09:00-17:00 (weekday range, then "
            "start and end time).")
    lo, hi, sh, sm, eh, em = (int(x) for x in m.groups())
    if lo > hi:
        raise BookingConfigError("The first weekday must not be after the last.")
    if sh > 23 or eh > 23:
        raise BookingConfigError("Hours must be between 00:00 and 23:59.")
    if (sh, sm) >= (eh, em):
        raise BookingConfigError("The end time must be after the start time.")
    return m.string


def _validate_timezone(name):
    name = str(name or "").strip()
    # A named zone, never a fixed offset. "UTC-9" is right for Alaska in
    # January and an hour wrong in July, and the failure looks like a client
    # arriving at the wrong time rather than like an error.
    if not name or "/" not in name:
        raise BookingConfigError(
            "Timezone must be a named zone such as America/Anchorage, not a "
            "fixed offset (offsets are wrong for half of every year wherever "
            "daylight saving applies).")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise BookingConfigError(f"Unknown timezone: {name}")
    return name


def _validate_session_types(items):
    if not isinstance(items, list):
        raise BookingConfigError("Session types must be a list.")
    if len(items) > MAX_SESSION_TYPES:
        raise BookingConfigError(f"At most {MAX_SESSION_TYPES} session types.")
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            raise BookingConfigError("Each session type must be an object.")
        slug = str(it.get("slug") or "").strip().lower()
        if not _SLUG_RE.match(slug):
            raise BookingConfigError(
                "Session type ids must be lowercase letters, digits and hyphens.")
        if slug in seen:
            raise BookingConfigError(f"Duplicate session type id: {slug}")
        seen.add(slug)
        label = _text(it.get("label"), MAX_LABEL)
        if not label:
            raise BookingConfigError(f"Session type {slug} needs a label.")
        dur = it.get("duration_min")
        if not isinstance(dur, int) or isinstance(dur, bool) \
                or not (MIN_DURATION <= dur <= MAX_DURATION):
            raise BookingConfigError(
                f"Session type {slug} needs a duration between "
                f"{MIN_DURATION} and {MAX_DURATION} minutes.")
        medium = str(it.get("medium") or "").strip().lower()
        if medium not in MEDIA:
            raise BookingConfigError(
                f"Session type {slug} medium must be one of {', '.join(MEDIA)}.")
        out.append({"slug": slug, "label": label,
                    "duration_min": dur, "medium": medium})
    return out


def validate_config(cfg) -> dict:
    """Clean and check a config, or raise BookingConfigError.

    Every message is written to be shown to the practitioner, so it says what
    to do rather than which field failed a regex.
    """
    if not isinstance(cfg, dict):
        raise BookingConfigError("Config must be an object.")
    notice = cfg.get("notice_hours", 24)
    buffer_min = cfg.get("buffer_min", 0)
    for name, val, hi in (("notice_hours", notice, 720), ("buffer_min", buffer_min, 240)):
        if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= hi):
            raise BookingConfigError(f"{name} must be a whole number between 0 and {hi}.")
    return {"timezone": _validate_timezone(cfg.get("timezone") or DEFAULT_TIMEZONE),
            "office_hours": _validate_hours(cfg.get("office_hours")),
            "session_types": _validate_session_types(cfg.get("session_types") or []),
            "notice_hours": notice, "buffer_min": buffer_min,
            "enabled": bool(cfg.get("enabled"))}


def get_config(cx, pid):
    try:
        row = cx.execute(
            "SELECT timezone, office_hours, session_types, notice_hours, "
            "buffer_min, enabled FROM practitioner_booking_config "
            "WHERE practitioner_id=?", (str(pid),)).fetchone()
    except db.Error:
        return None
    if not row:
        return None
    try:
        types = json.loads(row["session_types"])
    except (ValueError, TypeError):
        # A row we cannot read offers NO slots. It must not fall back to a
        # default, because a default here is somebody else's working day.
        return None
    return {"timezone": row["timezone"], "office_hours": row["office_hours"],
            "session_types": types, "notice_hours": row["notice_hours"],
            "buffer_min": row["buffer_min"], "enabled": bool(row["enabled"])}


def set_config(cx, pid, cfg) -> dict:
    clean = validate_config(cfg)
    from datetime import datetime, timezone as _tz
    cx.execute(
        "INSERT INTO practitioner_booking_config (practitioner_id, timezone, "
        "office_hours, session_types, notice_hours, buffer_min, enabled, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(practitioner_id) DO UPDATE SET timezone=excluded.timezone, "
        "office_hours=excluded.office_hours, session_types=excluded.session_types, "
        "notice_hours=excluded.notice_hours, buffer_min=excluded.buffer_min, "
        "enabled=excluded.enabled, updated_at=excluded.updated_at",
        (str(pid), clean["timezone"], clean["office_hours"],
         json.dumps(clean["session_types"]), clean["notice_hours"],
         clean["buffer_min"], 1 if clean["enabled"] else 0,
         datetime.now(_tz.utc).isoformat()))
    cx.commit()
    return clean


def is_bookable(cx, pid) -> bool:
    """Bookable means: a config exists, it is enabled, and it offers at least
    one session type. Fails closed on every other answer."""
    cfg = get_config(cx, pid)
    return bool(cfg and cfg["enabled"] and cfg["session_types"])
