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
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone as _tz
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

# evox_bookings.session_type is a shared, practitioner-agnostic column: three
# existing production flows branch on these exact literals with NO
# practitioner filter at all --
#   - app.py:_get_consult_booked / /api/consult/join select
#     "session_type='biofield-consult'" across EVERY practitioner's rows, and
#     /api/consult/join hands back GLEN_PMI_URL (his personal Zoom meeting)
#     to whoever it matches -- a practitioner naming her own session type
#     "biofield-consult" would hand her clients Glen's personal meeting URL
#     the moment their booking time enters the join window.
#   - app.py's reminder cron and dashboard/portal_calendar.py both branch on
#     "onboarding" (Rae's welcome call) and "triage" (the discovery-call
#     flow) to choose wording/labels for a session that is not theirs.
# A practitioner cannot pick one of these; reject at the door with a message
# that tells her to choose a different name, not a regex complaint.
RESERVED_SESSION_TYPES = frozenset({"biofield-consult", "onboarding", "triage"})


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
    # Missing/blank/non-string is a hard reject, not a substitution.
    # DEFAULT_TIMEZONE is a *pre-fill suggestion* for the form to offer --
    # a different job from validation. A blank timezone that slipped through
    # (unselected dropdown, a JS error, anything) must never silently become
    # Pacific/Honolulu; that is somebody else's working day.
    if not isinstance(name, str) or not name.strip():
        raise BookingConfigError(
            "Timezone is required. Pick the practitioner's own time zone; "
            "there is no safe default to fall back to.")
    name = name.strip()
    # A named zone, never a fixed offset. "UTC-9" is right for Alaska in
    # January and an hour wrong in July, and the failure looks like a client
    # arriving at the wrong time rather than like an error.
    if "/" not in name:
        raise BookingConfigError(
            "Timezone must be a named zone such as America/Anchorage, not a "
            "fixed offset (offsets are wrong for half of every year wherever "
            "daylight saving applies).")
    # Etc/GMT+9 contains a slash and resolves cleanly through ZoneInfo, so it
    # passes the check above -- but it is exactly the fixed-offset bug in a
    # different spelling: no DST awareness, AND the POSIX sign convention is
    # inverted (Etc/GMT+9 means UTC MINUS 9). A practitioner reaching for
    # "+9" would land nine hours the wrong way. Disqualified because it
    # names an offset, not a place -- not because it lacks DST (Hawaii has
    # none either, and it's the default).
    # Compared case-insensitively: ZoneInfo itself resolves "etc/GMT+9" and
    # "ETC/GMT+9" (at least on some platforms/filesystems), so a bare
    # startswith("Etc/") is a one-character-case bypass around the same bug.
    if name.lower().startswith("etc/"):
        raise BookingConfigError(
            "Timezone must be a named zone that describes a place (such as "
            "America/Anchorage), not an Etc/GMT offset. Etc/ zones never "
            "observe daylight saving and their sign is backwards from what "
            "the name suggests (Etc/GMT+9 means UTC minus 9).")
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
        if slug in RESERVED_SESSION_TYPES:
            raise BookingConfigError(
                f"\"{slug}\" is reserved for an existing appointment type. "
                "Please pick a different name for this session type.")
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
    return {"timezone": _validate_timezone(cfg.get("timezone")),
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
        timezone = _validate_timezone(row["timezone"])
        office_hours = _validate_hours(row["office_hours"])
    except (ValueError, TypeError):
        # A row we cannot read -- or cannot re-validate -- offers NO slots.
        # It must not fall back to a default, because a default here is
        # somebody else's working day. Applies equally to a corrupt JSON
        # blob, a hand-edited timezone, and a hand-edited hours string: all
        # three could reach this row without ever going through
        # validate_config (a migration, a partial write, direct SQL).
        return None
    return {"timezone": timezone, "office_hours": office_hours,
            "session_types": types, "notice_hours": row["notice_hours"],
            "buffer_min": row["buffer_min"], "enabled": bool(row["enabled"])}


def set_config(cx, pid, cfg) -> dict:
    clean = validate_config(cfg)
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


def now_in(tz_name):
    """Naive wall-clock 'now' in the named zone.

    Naive on purpose: dashboard.evox.available_slots compares against a naive
    grid, and mixing aware and naive datetimes raises. The zone is applied
    here and then dropped, which is the same shape as app.py's _hst_now()
    except that the offset comes from the zone database rather than a
    hardcoded -10, so it is correct on both sides of a daylight-saving change.
    """
    try:
        z = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        z = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(_tz.utc).astimezone(z).replace(tzinfo=None)


def _session_type(cfg, slug):
    for st in cfg["session_types"]:
        if st["slug"] == slug:
            return st
    return None


def slots_for(cx, pid, *, days, session_slug, booked, busy=()):
    """Open slots for one practitioner and session type, as naive ISO strings
    in HER timezone.

    Returns [] rather than raising for every "cannot answer" case: no config,
    disabled, unknown session type. A public page asks this question, and an
    empty list is a page that says "no times available" while an exception is
    a 500.

    `busy` is external busy intervals. In 3a nothing supplies it and it stays
    empty; plan 3b passes a practitioner's Google Calendar through here. The
    parameter exists now so 3b changes a caller rather than this signature.
    """
    cfg = get_config(cx, pid)
    if not cfg or not cfg["enabled"]:
        return []
    st = _session_type(cfg, session_slug)
    if not st:
        return []
    from dashboard import evox as _ev
    now = now_in(cfg["timezone"]) + timedelta(hours=cfg["notice_hours"])
    duration = st["duration_min"] + cfg["buffer_min"]
    return _ev.available_slots(days, cfg["office_hours"], list(busy), booked,
                               now, duration_min=duration)


def to_visitor_tz(iso, practitioner_tz, visitor_tz):
    """Render a naive practitioner-local slot as an offset-bearing ISO string
    in the visitor's zone.

    The offset comes from the zone rules ON THAT DATE, not today's, so a July
    slot rendered in January still says AKDT. Falls back to the practitioner's
    own zone for an unusable visitor zone: a browser can report anything, and
    a public booking page must not raise because of it.
    """
    try:
        pz = ZoneInfo(practitioner_tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        pz = ZoneInfo(DEFAULT_TIMEZONE)
    try:
        vz = ZoneInfo(visitor_tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        vz = pz
    aware = datetime.fromisoformat(str(iso)[:19]).replace(tzinfo=pz)
    return aware.astimezone(vz).isoformat()


def effective_visitor_tz(visitor_tz, practitioner_tz):
    """The zone name slots are actually rendered in, resolved once.

    to_visitor_tz falls back silently to the practitioner's zone when the
    visitor's zone is unusable, and tells its caller nothing about that
    fallback. A visitor whose browser reports a broken zone would otherwise
    read times labelled with what THEY sent (Mars/Olympus) while the values
    are actually in the practitioner's zone -- wrong and undetectable from
    the response. The public route resolves the effective zone here, once,
    and returns it in the payload so the page can label honestly from what
    was actually used, never from what the browser sent.
    """
    try:
        ZoneInfo(str(visitor_tz))
        return str(visitor_tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        pass
    try:
        ZoneInfo(str(practitioner_tz))
        return str(practitioner_tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        return DEFAULT_TIMEZONE


def resolve_practitioner_pid(cx, slug):
    """Map a public slug to a practitioner id, or None.

    Reads the same approved-affiliate row the public profile does, then the
    practitioner row keyed by that email. Fails closed: a missing table or a
    broken read means 'no such practitioner', never an exception on a public
    page.
    """
    try:
        row = cx.execute("SELECT email FROM affiliate_signups "
                         "WHERE slug=? AND status='approved'", (str(slug),)).fetchone()
    except db.Error:
        return None
    if not row or not (row["email"] or "").strip():
        return None
    from dashboard import practitioner_portal as _pp
    try:
        return _pp.find_practitioner_id_by_email(row["email"].strip().lower())
    except Exception:
        return None


def cancel_token(pid, start_ts, booking_id):
    """A token the client can use to cancel without an account.

    HMAC over (practitioner, slot, booking id) with the app secret, so it
    needs no storage and cannot be guessed. It is not a session: it
    authorises exactly one action on exactly one booking.

    booking_id is load-bearing, not decorative. A token built from only
    (practitioner, slot) is a pure function of the SLOT, so it stays valid
    forever -- including after that slot is cancelled and rebooked by a
    different client. The confirmation email is the only record a client
    without an account has of their appointment, and we tell them to keep it
    for the cancel link; a token that outlives its own booking turns that
    saved email into a permanent cancel credential for whoever books the
    same time next. Binding the booking's row id means a cancel-then-rebook
    on the same slot mints a genuinely different token, and the old email's
    link stops working the moment the row it names is gone.
    """
    secret = (os.environ.get("SECRET_KEY") or os.environ.get("CONSOLE_SECRET") or "dev")
    msg = f"{pid}|{start_ts}|{booking_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def cancel_token_ok(pid, start_ts, booking_id, token):
    return hmac.compare_digest(cancel_token(pid, start_ts, booking_id), str(token or ""))
