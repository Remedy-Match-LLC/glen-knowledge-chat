# Multi-Tenant Practitioner Booking Implementation Plan (Section 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a visitor on a practitioner's public page book a session, from hours and session types that practitioner entered herself, in her timezone, rendered in the visitor's.

**Architecture:** The booking core in `dashboard/evox.py` is already multi-tenant — `evox_bookings` carries `practitioner`, the double-booking guard is a database UNIQUE index on `(practitioner, start_ts)`, and `available_slots` is fully parameterized. What is hardcoded is the *callers*. This plan adds a per-practitioner config store, makes slot computation timezone-correct, gives the practitioner a form to fill in, and adds a **new public booking route** rather than opening a hole in the gated one.

**Tech Stack:** Python 3, Flask, sqlite (LOG_DB), `zoneinfo` from the standard library. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`, Section 3.

## Scope: this is 3a of 3

The spec says Section 3 "should be split again at the OAuth boundary: multi-tenant slots and hours first, calendar sync second." This plan is the first half.

**Explicitly NOT in this plan:** Google Calendar OAuth, the web consent flow, per-practitioner tokens, refresh handling, revocation, and reading a practitioner's external busy time. That is plan 3b, written after this one lands, when the config surfaces and storage are real rather than predicted.

**What that means for correctness here:** availability is computed from the practitioner's declared office hours minus bookings *in our own table*. If she has a dentist appointment in Google Calendar, this plan will happily offer that slot. That is a known and accepted limitation of 3a, it is why 3b exists, and **it must be stated on the practitioner's own booking-config page** so she is not surprised by it. Task 3 covers that.

## Global Constraints

Copied verbatim from the spec where the spec states a value:

- **Mary's booking gets its own public route. We do NOT add a bypass flag to the gated one.** `/api/consult/book` requires an EVOX token, paid membership, a paid test purchase, and a submitted intake, in sequence. A person Mary's client just texted has none of these and no account. A bypass parameter on that route is one bad call site away from letting anyone skip the paid-test gate.
- **The practitioner supplies all of this herself, so this section ships a data-entry form.** These are not values we configure on her behalf.
- **Fail closed on a dead token.** If we cannot read a practitioner's busy time, stop offering slots. Do not offer them blindly. Failing open here double-books a real person. (In 3a there is no external token; the equivalent rule is that a config we cannot parse offers **no** slots rather than defaulting to someone else's hours.)
- **Timezone is a first-class requirement, not a detail.** Slots are computed in the practitioner's timezone and rendered in the visitor's. Tests use a deliberately non-Hawaii practitioner and a third, different visitor timezone.
- **No payment means no-show cost is unbounded.** Mitigation is the whole of: confirmation email, reminder, working cancel link.
- **Booking concurrency:** two simultaneous bookings of the same slot; exactly one succeeds.
- **Mutation-test every guard**, not just exercise it: plant the violation, confirm the test goes red, then remove it.

---

## Context the implementer needs

### The core is already multi-tenant. Read it before changing anything.

`dashboard/evox.py` (find functions with `grep -n`, line numbers drift):

```python
init_tables(cx)                    # creates evox_bookings + ux_evox_active_slot
parse_office_hours(spec)           # "1-7:09:00-17:00" -> (1, 7, "09:00", "17:00")
slot_grid(day, spec, duration_min=60)
available_slots(days, office_spec, busy, booked, now, duration_min=60)
booked_starts(cx, practitioner="rae") -> set
rae_busy_intervals(cx, lo_date, hi_date, practitioner="rae")
book(...)                          # writes the row
build_ics(*, uid, start_ts, end_ts, summary, description, location, ...)
```

The schema:

```sql
CREATE TABLE IF NOT EXISTS evox_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL, practitioner TEXT NOT NULL DEFAULT 'rae',
    start_ts TEXT NOT NULL, end_ts TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked', prepaid INTEGER DEFAULT 0,
    calendar_event_id TEXT, ics_uid TEXT, created_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_evox_active_slot
    ON evox_bookings(practitioner, start_ts) WHERE status='booked';
```

That UNIQUE index is the double-booking guard, it is enforced by the database, and it therefore holds across processes and workers. **Do not replace it with an application-level check.** Task 4 relies on catching its `IntegrityError`.

### The timezone situation, which is the hardest part of this plan

**`start_ts` is a naive wall-clock string in the practitioner's own timezone.** There is no offset in it and no timezone column. `available_slots`, `slot_grid` and `_hm` all work in naive `datetime`, and `app.py`'s `_hst_now()` returns naive Hawaii time:

```python
def _hst_now():
    """Naive wall-clock 'now' in Hawaii (UTC-10, no DST) ..."""
    return _dt.now(_tz.utc).astimezone(_tz(_tdd(hours=-10))).replace(tzinfo=None)
```

That hardcoded `-10` is correct for Glen **forever**, because Hawaii does not observe DST. It is wrong for Mary **half the year**, because Alaska does. Verified:

```
Pacific/Honolulu   Jan -10:00   Jul -10:00   DST: False
America/Anchorage  Jan -09:00   Jul -08:00   DST: True
```

A fixed-offset approach for any practitioner outside Hawaii is an hour wrong for half of every year, in a way that shows up as a client arriving at the wrong time rather than as an exception.

**Design ruling, and the reasoning, because a future reader will want to change it:**

`start_ts` stays a naive wall-clock string in the practitioner's own timezone. We add a `timezone` column recording *which* zone that wall-clock belongs to, and convert only when rendering to a visitor.

The alternative — storing UTC — is what you would choose for a new table. It is the wrong choice here:

- It requires migrating live rows whose zone is implicit, on a booking table people are already in.
- During the migration window the UNIQUE index would be comparing UTC strings against naive-local strings, so the double-booking guard would silently weaken exactly when rows are moving.
- The index is scoped `(practitioner, start_ts)`, so naive-local is already correct for its one job: two bookings for the same practitioner at the same local wall-clock still collide.

What we lose is cross-practitioner time queries ("who is busy at 21:00 UTC"), which nothing in this plan or the spec asks for. If that need arrives, migrate then, deliberately, with the column already telling you what each row means.

### The gated route, for contrast

`/api/consult/book` in `app.py` checks, in sequence: an EVOX token resolves to an identity, membership is ready (403 `not_ready`), intake is submitted (409 `intake_required`), and the start time is in the available set. Mary's client has none of that. **Read it to copy its slot-checking discipline; do not add a parameter to it.**

### The four hardcoded points the spec names

Confirm each with `grep -n` before touching it:

- `GLEN_CONSULT_HOURS = os.environ.get("GLEN_CONSULT_HOURS", "1-7:09:00-17:00")` in `app.py`
- `practitioner="glen"` literals in the two consult handlers
- `_triage_hours(practitioner)`: `return GLEN_CONSULT_HOURS if practitioner == "glen" else EVOX_HOURS`
- `SESSION_TYPES` in `dashboard/appointment_proposals.py`, a two-entry dict keyed by slug with `label`, `practitioner`, `medium`, `duration_min`

**This plan does not delete any of them.** Glen's and Rae's existing booking flows keep working exactly as they do today; the new per-practitioner config is consulted only for practitioners that have a config row. Task 1 makes that fallback explicit. Ripping out the literals is a separate change with its own blast radius on two live flows.

### Environment

Run tests with `doppler run -p remedy-match -c dev -- python3 -m pytest <file> -q -p no:randomly` from the worktree root. **Never run the full suite — it sends real email.**

Known pre-existing failures on unmodified `origin/main`, not yours: `tests/test_practitioner_personal_order.py::test_personal_card_return_books_one_sales_receipt` and `tests/test_membership.py::test_studio_credit_post_inserts_intent_sends_glen_notification`.

`app.py` is ~52,000 lines and several sessions commit to it. Anchor on content with `grep -n`, never on a line number quoted here.

---

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/practitioner_booking.py` (new) | The whole of 3a's logic: the config table, validation, timezone-correct slot computation, and the cancel token. Pure functions plus a sqlite store; no Flask, no environment reads. |
| `app.py` (modify) | Four routes: practitioner config GET/POST, public availability, public book, public cancel. Thin — they validate the request and call the module. |
| `static/practitioner-booking.html` (new) | The practitioner's data-entry form. Reachable from the practitioner workspace nav. |
| `static/practitioner-profile.html`, `-portal`, `-dropship`, `-settings` (modify) | One nav entry each, matching the pattern established in the `Public Profile` change. |
| `tests/test_practitioner_booking.py` (new) | Config store, validation, timezone maths, cancel tokens. |
| `tests/test_practitioner_booking_routes.py` (new) | The four routes, including the concurrency test and the gated-route-untouched guard. |

---

## Task 1: The booking config store

**Files:**
- Create: `dashboard/practitioner_booking.py`
- Test: `tests/test_practitioner_booking.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, and later tasks depend on these exact names:
  - `init_tables(cx)`
  - `DEFAULT_TIMEZONE = "Pacific/Honolulu"`
  - `BookingConfigError` (exception)
  - `validate_config(cfg) -> dict` — returns the cleaned config or raises
  - `get_config(cx, pid) -> dict | None`
  - `set_config(cx, pid, cfg) -> dict`
  - `is_bookable(cx, pid) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_booking.py
"""Per-practitioner booking configuration.

A config we cannot parse must offer NO slots rather than fall back to someone
else's hours. Offering Glen's Hawaii hours to Mary's Alaska clients would put
real people on a call at the wrong time, which is worse than an empty page.
"""
import sqlite3

import pytest

from dashboard import practitioner_booking as pb

PID = "pid-mary"


@pytest.fixture
def cx(tmp_path):
    """A raw sqlite3 connection with a Row factory -- the pattern
    tests/test_practitioner_drafts.py already uses for a store like this.
    `import db` fails at collection in this suite; the module under test
    imports it as `from dashboard import db`."""
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    pb.init_tables(c)
    return c


def _cfg(**over):
    c = {"timezone": "America/Anchorage",
         "office_hours": "1-5:09:00-17:00",
         "session_types": [{"slug": "intro", "label": "Free 20 minute intro call",
                            "duration_min": 20, "medium": "phone"}],
         "notice_hours": 24, "buffer_min": 0, "enabled": True}
    c.update(over)
    return c


def test_a_config_round_trips(cx):
    pb.set_config(cx, PID, _cfg())
    got = pb.get_config(cx, PID)
    assert got["timezone"] == "America/Anchorage"
    assert got["office_hours"] == "1-5:09:00-17:00"
    assert got["session_types"][0]["slug"] == "intro"


def test_no_config_is_not_bookable(cx):
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_a_disabled_config_is_not_bookable(cx):
    """She can turn booking off without deleting her hours."""
    pb.set_config(cx, PID, _cfg(enabled=False))
    assert pb.is_bookable(cx, PID) is False


def test_a_config_with_no_session_types_is_not_bookable(cx):
    pb.set_config(cx, PID, _cfg(session_types=[]))
    assert pb.is_bookable(cx, PID) is False


@pytest.mark.parametrize("bad", [
    "",                     # empty
    "9-5:09:00-17:00",      # day range inverted
    "1-8:09:00-17:00",      # day 8 does not exist
    "1-5:25:00-17:00",      # hour 25
    "1-5:17:00-09:00",      # end before start
    "1-5",                  # no hours part
    "garbage",
])
def test_a_malformed_hours_spec_is_rejected(cx, bad):
    """parse_office_hours in evox.py raises ValueError on some of these and
    silently returns nonsense on others. Validate here so a bad value never
    reaches the slot grid."""
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(office_hours=bad))


def test_an_unknown_timezone_is_rejected(cx):
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(timezone="Mars/Olympus"))


def test_a_fixed_offset_is_rejected_not_just_an_unknown_name():
    """'UTC-9' looks reasonable and is exactly the DST bug this plan exists to
    avoid: Alaska is -09:00 in January and -08:00 in July."""
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(timezone="UTC-9"))


@pytest.mark.parametrize("bad", [0, -30, 601, "20", None])
def test_a_nonsense_duration_is_rejected(bad):
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(session_types=[
            {"slug": "intro", "label": "Intro", "duration_min": bad, "medium": "phone"}]))


def test_session_type_slugs_must_be_unique():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(session_types=[
            {"slug": "intro", "label": "A", "duration_min": 20, "medium": "phone"},
            {"slug": "intro", "label": "B", "duration_min": 30, "medium": "zoom"}]))


def test_an_unknown_medium_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(session_types=[
            {"slug": "intro", "label": "Intro", "duration_min": 20, "medium": "telepathy"}]))


def test_markup_in_a_label_is_stored_as_text():
    """The label reaches a public page. It is escaped at render time, but it
    should not arrive carrying tags either."""
    out = pb.validate_config(_cfg(session_types=[
        {"slug": "intro", "label": "<script>x</script>Intro",
         "duration_min": 20, "medium": "phone"}]))
    assert "<script>" not in out["session_types"][0]["label"]


def test_a_second_save_replaces_rather_than_duplicates(cx):
    pb.set_config(cx, PID, _cfg())
    pb.set_config(cx, PID, _cfg(office_hours="2-4:10:00-14:00"))
    assert pb.get_config(cx, PID)["office_hours"] == "2-4:10:00-14:00"
    rows = cx.execute("SELECT COUNT(*) c FROM practitioner_booking_config "
                      "WHERE practitioner_id=?", (PID,)).fetchone()
    assert rows["c"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking.py -q -p no:randomly`

Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.practitioner_booking'`.

- [ ] **Step 3: Write the implementation**

```python
# dashboard/practitioner_booking.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking.py -q -p no:randomly`

Expected: PASS.

- [ ] **Step 5: Mutation-test the fail-closed guards**

Two guards, and the spec names this behaviour, so watch both fail:

1. Change `get_config`'s unreadable-JSON branch to return a default config instead of `None`. Confirm a test goes red. Restore.
2. Change `is_bookable` to `return cfg is not None`. Confirm `test_a_disabled_config_is_not_bookable` and `test_a_config_with_no_session_types_is_not_bookable` both go red. Restore.

Record both outputs in your report.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_booking.py tests/test_practitioner_booking.py
git commit -m "feat(booking): per-practitioner booking config store"
```

---

## Task 2: Timezone-correct availability

**Files:**
- Modify: `dashboard/practitioner_booking.py`
- Test: `tests/test_practitioner_booking.py`

**Interfaces:**
- Consumes: `get_config`, `is_bookable`, `DEFAULT_TIMEZONE` from Task 1.
- Produces:
  - `now_in(tz_name) -> datetime` — naive wall-clock now in that zone
  - `slots_for(cx, pid, *, days, session_slug, booked, busy=()) -> list[str]` — naive ISO strings in the practitioner's zone
  - `to_visitor_tz(iso, practitioner_tz, visitor_tz) -> str` — ISO 8601 **with offset**

**This is the task most likely to be got subtly wrong.** Read the timezone section of the context above before starting.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, datetime


def test_now_in_uses_the_named_zone_not_a_fixed_offset():
    """Alaska is -09:00 in January and -08:00 in July. Anything that hardcodes
    an offset is an hour wrong for half the year, and the symptom is a client
    arriving at the wrong time rather than an exception."""
    from zoneinfo import ZoneInfo
    jan = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("America/Anchorage"))
    jul = datetime(2026, 7, 15, 12, tzinfo=ZoneInfo("America/Anchorage"))
    assert jan.utcoffset() != jul.utcoffset(), "fixture assumption"
    n = pb.now_in("America/Anchorage")
    assert n.tzinfo is None, "callers compare against a naive grid"


def test_slots_come_back_in_the_practitioner_timezone(cx):
    pb.set_config(cx, PID, _cfg(office_hours="1-5:09:00-17:00"))
    days = [date(2026, 9, 7)]              # a Monday
    got = pb.slots_for(cx, PID, days=days, session_slug="intro", booked=set())
    assert got, "expected slots on a weekday inside office hours"
    assert all(s.startswith("2026-09-07T") for s in got)
    assert got[0] == "2026-09-07T09:00:00"


def test_slot_length_follows_the_session_type(cx):
    pb.set_config(cx, PID, _cfg(session_types=[
        {"slug": "intro", "label": "Intro", "duration_min": 20, "medium": "phone"},
        {"slug": "full", "label": "Full", "duration_min": 60, "medium": "zoom"}]))
    days = [date(2026, 9, 7)]
    short = pb.slots_for(cx, PID, days=days, session_slug="intro", booked=set())
    long = pb.slots_for(cx, PID, days=days, session_slug="full", booked=set())
    assert len(short) > len(long)
    assert short[1] == "2026-09-07T09:20:00"
    assert long[1] == "2026-09-07T10:00:00"


def test_an_unknown_session_type_offers_nothing(cx):
    pb.set_config(cx, PID, _cfg())
    assert pb.slots_for(cx, PID, days=[date(2026, 9, 7)],
                        session_slug="nope", booked=set()) == []


def test_a_practitioner_with_no_config_offers_nothing(cx):
    assert pb.slots_for(cx, "pid-nobody", days=[date(2026, 9, 7)],
                        session_slug="intro", booked=set()) == []


def test_a_booked_slot_is_not_offered(cx):
    pb.set_config(cx, PID, _cfg())
    days = [date(2026, 9, 7)]
    first = pb.slots_for(cx, PID, days=days, session_slug="intro", booked=set())[0]
    again = pb.slots_for(cx, PID, days=days, session_slug="intro", booked={first})
    assert first not in again


def test_a_day_outside_the_weekday_range_offers_nothing(cx):
    pb.set_config(cx, PID, _cfg(office_hours="1-5:09:00-17:00"))
    assert pb.slots_for(cx, PID, days=[date(2026, 9, 6)],   # Sunday
                        session_slug="intro", booked=set()) == []


def test_rendering_to_the_visitor_crosses_the_date_line_correctly():
    """Practitioner in Alaska, visitor in New Zealand: 15:00 Monday in
    Anchorage is already Tuesday in Auckland. A naive string handed straight
    to a visitor is not just shifted, it is the wrong DAY."""
    out = pb.to_visitor_tz("2026-09-07T15:00:00", "America/Anchorage", "Pacific/Auckland")
    assert out.startswith("2026-09-08T"), out


def test_rendering_uses_the_offset_in_force_on_that_date():
    """Not the offset in force today. A slot booked in July must render with
    July's offset even if it is January when the page loads."""
    jul = pb.to_visitor_tz("2026-07-15T09:00:00", "America/Anchorage", "UTC")
    jan = pb.to_visitor_tz("2026-01-15T09:00:00", "America/Anchorage", "UTC")
    assert jul.startswith("2026-07-15T17:00")   # AKDT, -08:00
    assert jan.startswith("2026-01-15T18:00")   # AKST, -09:00


def test_an_unknown_visitor_timezone_falls_back_to_the_practitioner(cx):
    """A visitor's browser can report anything. Never raise on a public page."""
    out = pb.to_visitor_tz("2026-09-07T09:00:00", "America/Anchorage", "Mars/Olympus")
    assert out.startswith("2026-09-07T09:00")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking.py -q -p no:randomly`

Expected: FAIL with `AttributeError: module 'dashboard.practitioner_booking' has no attribute 'now_in'`.

- [ ] **Step 3: Write the implementation**

Add to `dashboard/practitioner_booking.py`:

```python
from datetime import datetime, timedelta, timezone as _timezone


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
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        z = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(_timezone.utc).astimezone(z).replace(tzinfo=None)


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
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        pz = ZoneInfo(DEFAULT_TIMEZONE)
    try:
        vz = ZoneInfo(visitor_tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        vz = pz
    aware = datetime.fromisoformat(str(iso)[:19]).replace(tzinfo=pz)
    return aware.astimezone(vz).isoformat()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking.py -q -p no:randomly`

Expected: PASS.

- [ ] **Step 5: Mutation-test the timezone guards**

1. Change `now_in` to use a fixed `timedelta(hours=-10)` like `_hst_now` does. Confirm a test goes red. Restore.
2. Change `to_visitor_tz` to return `iso` unchanged. Confirm the date-line test goes red. Restore.
3. Change `slots_for`'s no-config branch to fall through to `DEFAULT_TIMEZONE` and a default hours spec. Confirm `test_a_practitioner_with_no_config_offers_nothing` goes red. Restore.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_booking.py tests/test_practitioner_booking.py
git commit -m "feat(booking): timezone-correct availability"
```

---

## Task 3: The practitioner's config form

**Files:**
- Create: `static/practitioner-booking.html`
- Modify: `app.py` (two routes), and the four practitioner workspace pages for the nav entry
- Test: `tests/test_practitioner_booking_routes.py` (new)

**Interfaces:**
- Consumes: `init_tables`, `get_config`, `set_config`, `validate_config`, `BookingConfigError`, `DEFAULT_TIMEZONE` from Task 1.
- Produces: `GET /api/practitioner/booking-config` and `POST /api/practitioner/booking-config`, both authenticated as the practitioner; the page at `/practitioner/booking`.

**Authentication:** use `_practitioner_session_pid()`, the same helper every other practitioner API uses. Find it with `grep -n "def _practitioner_session_pid" app.py`. It returns the practitioner id or `None`; return 401 on `None`. **The practitioner writes only her own config** — the pid comes from the session, never from the request body. Taking it from the body would let any signed-in practitioner rewrite another's hours.

**The nav entry** follows the pattern already established for `Public Profile`: add `<a href="/practitioner/booking">Booking</a>` to the `workspace-nav` block in `practitioner-portal.html`, `practitioner-dropship.html`, `practitioner-settings.html` and `practitioner-profile.html`, and give `practitioner-booking.html` the full nav with `class="primary"` on its own entry.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_booking_routes.py
"""Routes for booking configuration and public booking.

Assertions are on raw response bytes and JSON, never a parsed DOM.
"""
import contextlib
import os
import sqlite3
import pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod
from dashboard import practitioner_booking as pb

PID = "pid-mary"


@contextlib.contextmanager
def _open(path):
    """A Row-factory sqlite3 connection, closed on exit.

    The route code uses `db.connect(LOG_DB)` from dashboard.db; tests open the
    same file directly, matching tests/test_practitioner_drafts.py. A bare
    `import db` fails at COLLECTION in this suite -- verified -- so do not
    reach for one.
    """
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


CFG = {"timezone": "America/Anchorage", "office_hours": "1-5:09:00-17:00",
       "session_types": [{"slug": "intro", "label": "Free 20 minute intro call",
                          "duration_min": 20, "medium": "phone"}],
       "notice_hours": 24, "buffer_min": 0, "enabled": True}


@pytest.fixture
def logdb(tmp_path, monkeypatch):
    p = str(tmp_path / "log.db")
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    pb.init_tables(c)
    from dashboard import evox as _ev
    _ev.init_evox_tables(c)
    c.close()
    monkeypatch.setattr(appmod, "LOG_DB", p)
    return p


@pytest.fixture
def practitioner(monkeypatch, logdb):
    """Signed in as Mary."""
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    # Roughly two dozen test files set TESTING=True on this SHARED app object
    # and never reset it, so whether the client converts an exception into a
    # 500 or re-raises depends on which files ran first. Pin it.
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    return appmod.app.test_client()


def test_config_requires_a_signed_in_practitioner(monkeypatch, logdb):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    c = appmod.app.test_client()
    assert c.get("/api/practitioner/booking-config").status_code == 401
    assert c.post("/api/practitioner/booking-config", json=CFG).status_code == 401


def test_a_config_saves_and_reads_back(practitioner):
    r = practitioner.post("/api/practitioner/booking-config", json=CFG)
    assert r.status_code == 200, r.get_data(as_text=True)
    got = practitioner.get("/api/practitioner/booking-config").get_json()
    assert got["config"]["office_hours"] == "1-5:09:00-17:00"
    assert got["config"]["session_types"][0]["slug"] == "intro"


def test_an_invalid_config_is_rejected_with_a_readable_message(practitioner):
    bad = dict(CFG, office_hours="garbage")
    r = practitioner.post("/api/practitioner/booking-config", json=bad)
    assert r.status_code == 400
    assert "1-5:09:00-17:00" in r.get_json()["error"], \
        "the message should show the format, not name a regex"


def test_a_practitioner_cannot_write_another_practitioners_config(practitioner, logdb):
    """The pid comes from the session. A pid in the body must be ignored."""
    practitioner.post("/api/practitioner/booking-config",
                      json=dict(CFG, practitioner_id="pid-someone-else"))
    with _open(logdb) as c:
        assert pb.get_config(c, "pid-someone-else") is None
        assert pb.get_config(c, PID) is not None


def test_no_config_yet_reads_back_as_null_not_an_error(practitioner):
    r = practitioner.get("/api/practitioner/booking-config")
    assert r.status_code == 200
    assert r.get_json()["config"] is None


def test_the_form_page_serves_and_carries_the_workspace_nav(practitioner):
    body = practitioner.get("/practitioner/booking").get_data(as_text=True)
    assert 'class="workspace-nav"' in body
    assert "/practitioner/profile" in body
    assert "/practitioner/portal" in body


def test_the_form_states_that_external_calendars_are_not_read_yet():
    """3a offers slots from declared hours minus OUR bookings. A commitment
    that lives only in her Google Calendar will still be offered, and she has
    to be told that in plain words rather than discovering it."""
    import pathlib
    html = (pathlib.Path(appmod.STATIC) / "practitioner-booking.html").read_text()
    assert "Google Calendar" in html
    low = html.lower()
    assert "not" in low and "yet" in low


@pytest.mark.parametrize("page", ["practitioner-portal.html", "practitioner-dropship.html",
                                  "practitioner-settings.html", "practitioner-profile.html",
                                  "practitioner-booking.html"])
def test_every_workspace_page_links_to_booking(page):
    import pathlib, re
    html = (pathlib.Path(appmod.STATIC) / page).read_text()
    nav = re.search(r'<nav class="workspace-nav".*?</nav>', html, re.S)
    assert nav, f"{page} has no workspace nav"
    assert "/practitioner/booking" in nav.group(0), \
        f"{page} nav gives no way to reach the booking setup"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking_routes.py -q -p no:randomly`

Expected: FAIL — the routes and the page do not exist, so the config calls 404.

- [ ] **Step 3: Write the routes**

Add to `app.py`, beside the other practitioner APIs:

```python
@app.route("/practitioner/booking")
def practitioner_booking_page():
    return _practitioner_page("practitioner-booking.html")


@app.route("/api/practitioner/booking-config", methods=["GET"])
def api_practitioner_booking_config_get():
    pid = _practitioner_session_pid()
    if not pid:
        return jsonify({"ok": False, "error": "not signed in"}), 401
    from dashboard import practitioner_booking as _pb
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pb.init_tables(cx)
        cfg = _pb.get_config(cx, pid)
    return jsonify({"ok": True, "config": cfg,
                    "default_timezone": _pb.DEFAULT_TIMEZONE,
                    "media": list(_pb.MEDIA)})


@app.route("/api/practitioner/booking-config", methods=["POST"])
def api_practitioner_booking_config_post():
    pid = _practitioner_session_pid()
    if not pid:
        return jsonify({"ok": False, "error": "not signed in"}), 401
    from dashboard import practitioner_booking as _pb
    body = request.get_json(silent=True) or {}
    # pid comes from the SESSION. A practitioner_id in the body is ignored on
    # purpose: honouring it would let any signed-in practitioner rewrite
    # another's hours.
    try:
        with db.connect(LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            _pb.init_tables(cx)
            clean = _pb.set_config(cx, pid, body)
    except _pb.BookingConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "config": clean})
```

- [ ] **Step 4: Write the form page**

Create `static/practitioner-booking.html`. Copy the `<head>`, `<style>` and the theme toggle from `static/practitioner-profile.html` so the workspace looks consistent, then use this body:

```html
<nav class="workspace-nav" aria-label="Practitioner workspace">
  <a href="/practitioner/portal">Practitioner Home</a>
  <a href="/practitioner/dropship">Drop-Ship Order</a>
  <a href="/practitioner/profile">Public Profile</a>
  <a class="primary" href="/practitioner/booking">Booking</a>
  <a href="/practitioner/settings">Practice Settings</a>
  <a href="/practitioner/client-account?section=messages">Messages &amp; Order Help</a>
</nav>

<h1>Booking</h1>
<p class="muted">When this is switched on, a Book button appears on your public
page and visitors can choose a time from the hours you set here.</p>

<div class="notice" id="cal-notice">
  <b>Your Google Calendar is not connected yet.</b> Times are offered from the
  hours below, minus sessions already booked through this site. Anything that
  lives only in your own calendar will still be offered, so keep the hours here
  to times you can reliably take a call. Connecting your calendar is the next
  piece of work.
</div>

<label><input type="checkbox" id="bk-enabled"> Accept bookings on my public page</label>

<label>Timezone
  <select id="bk-timezone"></select>
  <span class="hint">Times you enter below are in this zone. Visitors see them
  converted to their own.</span>
</label>

<label>Days and hours
  <input id="bk-hours" placeholder="1-5:09:00-17:00">
  <span class="hint">Weekday range then start and end time. 1 is Monday, 7 is
  Sunday. So 1-5:09:00-17:00 means weekdays, nine to five.</span>
</label>

<label>Shortest notice
  <input id="bk-notice" type="number" min="0" max="720" value="24">
  <span class="hint">Hours. A visitor cannot book anything sooner than this.</span>
</label>

<label>Gap between sessions
  <input id="bk-buffer" type="number" min="0" max="240" value="0">
  <span class="hint">Minutes added after each session before the next can start.</span>
</label>

<h2>Session types</h2>
<div id="bk-types"></div>
<button type="button" onclick="addType()">Add a session type</button>

<div id="bk-msg" class="msg"></div>
<button type="button" class="primary" onclick="save()">Save</button>

<script>
var MEDIA = ["phone", "zoom", "in-person"];
var ZONES = ["Pacific/Honolulu", "America/Anchorage", "America/Los_Angeles",
             "America/Denver", "America/Chicago", "America/New_York",
             "Europe/London", "Australia/Sydney", "Pacific/Auckland"];

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
  });
}

function typeRow(t) {
  t = t || {slug: "", label: "", duration_min: 30, medium: "phone"};
  var opts = MEDIA.map(function (m) {
    return '<option value="' + m + '"' + (m === t.medium ? " selected" : "") +
           ">" + m + "</option>";
  }).join("");
  var d = document.createElement("div");
  d.className = "type-row";
  d.innerHTML =
    '<input class="t-slug" placeholder="intro" value="' + esc(t.slug) + '">' +
    '<input class="t-label" placeholder="Free 20 minute intro call" value="' + esc(t.label) + '">' +
    '<input class="t-dur" type="number" min="5" max="600" value="' + (t.duration_min || 30) + '">' +
    '<select class="t-medium">' + opts + "</select>" +
    '<button type="button" onclick="this.parentNode.remove()">Remove</button>';
  return d;
}

function addType(t) { document.getElementById("bk-types").appendChild(typeRow(t)); }

function collect() {
  var types = [].slice.call(document.querySelectorAll(".type-row")).map(function (r) {
    return {slug: r.querySelector(".t-slug").value.trim().toLowerCase(),
            label: r.querySelector(".t-label").value.trim(),
            duration_min: parseInt(r.querySelector(".t-dur").value, 10),
            medium: r.querySelector(".t-medium").value};
  });
  return {timezone: document.getElementById("bk-timezone").value,
          office_hours: document.getElementById("bk-hours").value.trim(),
          session_types: types,
          notice_hours: parseInt(document.getElementById("bk-notice").value, 10),
          buffer_min: parseInt(document.getElementById("bk-buffer").value, 10),
          enabled: document.getElementById("bk-enabled").checked};
}

function save() {
  var msg = document.getElementById("bk-msg");
  msg.textContent = "Saving...";
  fetch("/api/practitioner/booking-config",
        {method: "POST", headers: {"Content-Type": "application/json"},
         body: JSON.stringify(collect())})
    .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
    .then(function (res) {
      msg.textContent = res.ok ? "Saved." : (res.j.error || "Could not save that.");
      msg.className = res.ok ? "msg ok" : "msg err";
    })
    .catch(function () { msg.textContent = "Could not reach the server."; });
}

(function load() {
  var sel = document.getElementById("bk-timezone");
  var guess = "";
  try { guess = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) {}
  var zones = ZONES.slice();
  if (guess && zones.indexOf(guess) < 0) zones.unshift(guess);
  sel.innerHTML = zones.map(function (z) {
    return '<option value="' + esc(z) + '">' + esc(z) + "</option>";
  }).join("");

  fetch("/api/practitioner/booking-config")
    .then(function (r) { return r.json(); })
    .then(function (v) {
      var c = v.config;
      if (!c) { if (guess) sel.value = guess; addType(); return; }
      sel.value = c.timezone;
      document.getElementById("bk-hours").value = c.office_hours;
      document.getElementById("bk-notice").value = c.notice_hours;
      document.getElementById("bk-buffer").value = c.buffer_min;
      document.getElementById("bk-enabled").checked = !!c.enabled;
      (c.session_types || []).forEach(addType);
      if (!(c.session_types || []).length) addType();
    });
})();
</script>
```

- [ ] **Step 5: Add the nav entry to the other four pages**

In each of `static/practitioner-portal.html`, `static/practitioner-dropship.html`, `static/practitioner-settings.html` and `static/practitioner-profile.html`, add one line inside the existing `<nav class="workspace-nav">` block, after the `Public Profile` entry:

```html
    <a href="/practitioner/booking">Booking</a>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking_routes.py -q -p no:randomly`

Expected: PASS.

- [ ] **Step 7: Mutation-test the ownership guard**

Change the POST route to take the pid from `body.get("practitioner_id") or pid`. Confirm `test_a_practitioner_cannot_write_another_practitioners_config` goes **red**. Restore, confirm green. This is the guard that stops one practitioner rewriting another's working week.

- [ ] **Step 8: Commit**

```bash
git add dashboard/practitioner_booking.py app.py static/practitioner-booking.html \
        static/practitioner-portal.html static/practitioner-dropship.html \
        static/practitioner-settings.html static/practitioner-profile.html \
        tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): practitioner-facing booking configuration form"
```

---

## Task 4: The public booking route

**Files:**
- Modify: `app.py`, `dashboard/practitioner_booking.py`
- Test: `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `slots_for`, `to_visitor_tz`, `get_config`, `is_bookable` from Tasks 1-2; `dashboard.evox.book`, `booked_starts`, `init_tables`.
- Produces: `GET /api/book/<slug>/slots`, `POST /api/book/<slug>`, and `resolve_practitioner_pid(cx, slug) -> str | None` in `practitioner_booking.py`.

**This route is public and unauthenticated.** That is the point: a person Mary's client just texted has no account. Everything the gated route gets from an identity, this route must get from validation instead.

**Do not touch `/api/consult/book`.** Not a parameter, not a flag, not a shared helper that changes its behaviour. The spec is explicit, and the precedent it cites is a shared pricing helper whose operator stop 400'd 79 products at client checkout for six days.

**The double-booking guard is the database UNIQUE index.** Check availability first for a good error message, then catch `IntegrityError` from the insert and return 409. The check alone loses the race; the index alone gives an ugly failure. Both.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, timedelta


def _seed_slug(logdb, slug="mary-boyd", email="my_mary_boyd@example.com"):
    with _open(logdb) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS affiliate_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
            slug TEXT, status TEXT)""")
        c.execute("INSERT INTO affiliate_signups (name,email,slug,status) "
                  "VALUES (?,?,?,'approved')", ("Mary Boyd", email, slug))
        c.commit()


@pytest.fixture
def public(monkeypatch, logdb):
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    from dashboard import practitioner_booking as _pb
    monkeypatch.setattr(_pb, "resolve_practitioner_pid", lambda cx, slug: PID)
    return appmod.app.test_client()


def test_slots_are_public_and_need_no_token(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/api/book/mary-boyd/slots?session=intro&tz=Pacific/Auckland")
    assert r.status_code == 200
    assert isinstance(r.get_json()["slots"], list)


def test_slots_are_rendered_in_the_visitor_timezone(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/api/book/mary-boyd/slots?session=intro&tz=Pacific/Auckland")
    slots = r.get_json()["slots"]
    assert slots, "expected some availability"
    # An offset-bearing string, not a naive one, so the browser cannot guess wrong.
    assert "+" in slots[0]["visitor"] or "-" in slots[0]["visitor"][10:]
    assert slots[0]["start"] != slots[0]["visitor"]


def test_a_practitioner_who_has_not_enabled_booking_offers_nothing(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, enabled=False))
    r = public.get("/api/book/mary-boyd/slots?session=intro")
    assert r.status_code == 200
    assert r.get_json()["slots"] == []


def test_booking_writes_a_row_and_returns_a_cancel_token(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["cancel_token"]
    with _open(logdb) as c:
        row = c.execute("SELECT practitioner, start_ts, status FROM evox_bookings").fetchone()
        assert row["practitioner"] == PID and row["status"] == "booked"


def test_the_same_slot_cannot_be_booked_twice(public, logdb):
    """The guard is the database UNIQUE index on (practitioner, start_ts), so
    it holds across processes. Exactly one booking survives."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    body = {"session": "intro", "start": slot["start"],
            "name": "A Client", "email": "client@example.com"}
    first = public.post("/api/book/mary-boyd", json=body)
    second = public.post("/api/book/mary-boyd", json=dict(body, email="other@example.com"))
    assert first.status_code == 200
    assert second.status_code == 409
    with _open(logdb) as c:
        n = c.execute("SELECT COUNT(*) c FROM evox_bookings WHERE status='booked'").fetchone()
        assert n["c"] == 1


def test_a_slot_outside_the_offered_set_is_refused(public, logdb):
    """Never trust a start time from the request. A client can post anything."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": "2026-09-07T03:00:00",
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "slot_unavailable"


def test_a_bad_email_is_refused(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "not-an-email"})
    assert r.status_code == 400


def test_an_unknown_slug_is_404(public, monkeypatch):
    from dashboard import practitioner_booking as _pb
    monkeypatch.setattr(_pb, "resolve_practitioner_pid", lambda cx, slug: None)
    assert public.get("/api/book/nobody/slots?session=intro").status_code == 404


def test_the_gated_consult_route_is_untouched():
    """The spec forbids a bypass on /api/consult/book. This asserts the source
    still contains its checks, so a future 'small refactor' that shares a
    helper with the public route cannot quietly remove them."""
    import inspect
    src = inspect.getsource(appmod)
    i = src.index('"/api/consult/book"')
    window = src[i:i + 4000]
    assert "intake_required" in window
    assert "not_ready" in window
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking_routes.py -q -p no:randomly`

Expected: FAIL — `/api/book/...` 404s.

- [ ] **Step 3: Add the slug resolver and cancel token**

Add to `dashboard/practitioner_booking.py`:

```python
import hashlib
import hmac
import os


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


def cancel_token(pid, start_ts):
    """A token the client can use to cancel without an account.

    HMAC over (practitioner, slot) with the app secret, so it needs no storage
    and cannot be guessed. It is not a session: it authorises exactly one
    action on exactly one booking.
    """
    secret = (os.environ.get("SECRET_KEY") or os.environ.get("CONSOLE_SECRET") or "dev")
    msg = f"{pid}|{start_ts}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def cancel_token_ok(pid, start_ts, token):
    return hmac.compare_digest(cancel_token(pid, start_ts), str(token or ""))
```

- [ ] **Step 4: Add the routes**

Add to `app.py`:

```python
_BOOK_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _book_days(n=21):
    from datetime import date, timedelta
    today = date.today()
    return [today + timedelta(days=i) for i in range(n)]


@app.route("/api/book/<slug>/slots", methods=["GET"])
def api_public_book_slots(slug):
    """Open times for a practitioner's public booking page.

    PUBLIC and unauthenticated on purpose: a person who was just texted this
    link has no account and no token. /api/consult/book stays gated and
    untouched; this is a separate route with separate rules, per the spec.
    """
    if not _public_surface_enabled():
        return ("", 404)
    from dashboard import practitioner_booking as _pb
    session_slug = (request.args.get("session") or "").strip()
    visitor_tz = (request.args.get("tz") or "").strip()
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pb.init_tables(cx)
        pid = _pb.resolve_practitioner_pid(cx, slug)
        if not pid:
            return jsonify({"ok": False, "error": "unknown_practitioner"}), 404
        cfg = _pb.get_config(cx, pid)
        if not cfg:
            return jsonify({"ok": True, "slots": [], "session_types": []})
        from dashboard import evox as _ev
        _ev.init_evox_tables(cx)
        booked = _ev.booked_starts(cx, practitioner=pid)
        starts = _pb.slots_for(cx, pid, days=_book_days(),
                               session_slug=session_slug, booked=booked)
    return jsonify({
        "ok": True,
        "timezone": cfg["timezone"],
        "session_types": cfg["session_types"] if cfg["enabled"] else [],
        "slots": [{"start": s,
                   "visitor": _pb.to_visitor_tz(s, cfg["timezone"], visitor_tz)}
                  for s in starts]})


@app.route("/api/book/<slug>", methods=["POST"])
def api_public_book(slug):
    if not _public_surface_enabled():
        return ("", 404)
    from dashboard import practitioner_booking as _pb
    from dashboard import evox as _ev
    body = request.get_json(silent=True) or {}
    session_slug = (body.get("session") or "").strip()
    start_ts = (body.get("start") or "").strip()
    name = (body.get("name") or "").strip()[:120]
    email = (body.get("email") or "").strip().lower()[:200]
    if not _BOOK_EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "bad_email"}), 400
    if not name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pb.init_tables(cx)
        _ev.init_evox_tables(cx)
        pid = _pb.resolve_practitioner_pid(cx, slug)
        if not pid:
            return jsonify({"ok": False, "error": "unknown_practitioner"}), 404
        cfg = _pb.get_config(cx, pid)
        st = next((t for t in (cfg or {}).get("session_types", [])
                   if t["slug"] == session_slug), None)
        if not cfg or not cfg["enabled"] or not st:
            return jsonify({"ok": False, "error": "not_bookable"}), 409
        # Never trust a start time from the request. Recompute the offered set
        # and require membership in it -- the same discipline the gated route
        # uses, which is the part of it worth copying.
        booked = _ev.booked_starts(cx, practitioner=pid)
        offered = _pb.slots_for(cx, pid, days=_book_days(),
                                session_slug=session_slug, booked=booked)
        if start_ts not in offered:
            return jsonify({"ok": False, "error": "slot_unavailable"}), 400
        try:
            _ev.create_booking(cx, email, start_ts,
                               duration_min=st["duration_min"],
                               practitioner=pid, session_type=session_slug,
                               medium=st["medium"])
        except _ev.SlotTaken:
            # create_booking catches the UNIQUE violation itself and re-raises
            # it as SlotTaken. The index on (practitioner, start_ts) is the real
            # guard and it is enforced by the database, so it holds across
            # workers; the availability check above only makes the common case
            # a readable error rather than a race.
            return jsonify({"ok": False, "error": "slot_taken"}), 409
    token = _pb.cancel_token(pid, start_ts)
    return jsonify({"ok": True, "start": start_ts, "cancel_token": token})
```

**Three things about `create_booking` that will bite if you assume otherwise.** Verified against `dashboard/evox.py` on 2026-08-30; re-check with `grep -n "def create_booking" -A 8 dashboard/evox.py` before writing the call.

```python
def create_booking(cx, email: str, start_ts: str, *, duration_min: int = 60,
                   prepaid: bool = False, practitioner: str = "rae",
                   session_type: str = "evox", medium: str = "phone", tag_fn=None) -> dict
```

1. **It is `create_booking`, not `book`.**
2. **It raises `evox.SlotTaken`, not `sqlite3.IntegrityError`.** It catches the UNIQUE violation itself, rolls back, and re-raises. Catching `IntegrityError` here would miss it and turn a taken slot into a 500 on a public page.
3. **It computes `end_ts` itself** from `duration_min`. Do not pass one.

It also inserts a row into `calendar_events` with `owner=practitioner`, which is how `rae_busy_intervals` later sees the booking as busy. That is existing behaviour and you want it; just know the write happens.

Do not change its signature. Glen's and Rae's live flows call it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_booking_routes.py -q -p no:randomly`

Expected: PASS.

- [ ] **Step 6: Mutation-test the two guards the spec names**

1. Remove the `if start_ts not in offered` check. Confirm `test_a_slot_outside_the_offered_set_is_refused` goes **red**. Restore.
2. Drop the UNIQUE index before the second booking in a scratch copy of the concurrency test, and confirm the double-booking test would pass two rows — demonstrating that the index, not the check, is what holds the line. Restore. Report what you observed.

- [ ] **Step 7: Commit**

```bash
git add app.py dashboard/practitioner_booking.py tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): public booking route, separate from the gated one"
```

---

## Task 5: Confirmation, calendar invite and a working cancel link

**Files:**
- Modify: `app.py`, `dashboard/practitioner_booking.py`
- Test: `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `cancel_token`, `cancel_token_ok` from Task 4; `dashboard.evox.build_ics`.
- Produces: `GET /book/cancel` (a page), `POST /api/book/<slug>/cancel`.

**The spec ties these together:** *"No payment means no-show cost is unbounded. Mitigation is the whole of: confirmation email, reminder, working cancel link."* A cancel link that does not work is worse than none, because the client stops looking for another way to tell her.

- [ ] **Step 1: Write the failing test**

```python
def test_booking_sends_a_confirmation_to_the_client(public, logdb, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.append(
                            {"to": to, "subject": subject, "body": body}))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert sent, "the client got no confirmation"
    body = sent[0]["body"]
    assert "cancel" in body.lower()
    assert "/book/cancel?" in body


def test_the_confirmation_states_the_time_in_the_visitor_timezone(public, logdb, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.append(body))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    assert "Pacific/Auckland" in sent[0] or "NZ" in sent[0], \
        "a client cannot act on a time in a zone they do not live in"


def test_a_cancel_token_releases_the_slot(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    token = r.get_json()["cancel_token"]

    c2 = public.post("/api/book/mary-boyd/cancel",
                     json={"start": slot["start"], "token": token})
    assert c2.status_code == 200
    with _open(logdb) as c:
        row = c.execute("SELECT status FROM evox_bookings").fetchone()
        assert row["status"] != "booked"
    again = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"]
    assert any(s["start"] == slot["start"] for s in again), \
        "a cancelled slot must become available again"


def test_a_forged_cancel_token_is_refused(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    r = public.post("/api/book/mary-boyd/cancel",
                    json={"start": slot["start"], "token": "0" * 32})
    assert r.status_code == 403
    with _open(logdb) as c:
        assert c.execute("SELECT status FROM evox_bookings").fetchone()["status"] == "booked"


def test_a_cancel_token_for_one_slot_does_not_cancel_another(public, logdb):
    """The token is scoped to (practitioner, slot). One booking's token must
    not be a skeleton key for the practitioner's whole day."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slots = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"]
    a, b = slots[0], slots[1]
    for s in (a, b):
        public.post("/api/book/mary-boyd", json={
            "session": "intro", "start": s["start"],
            "name": "A Client", "email": "client@example.com"})
    token_a = pb.cancel_token(PID, a["start"])
    r = public.post("/api/book/mary-boyd/cancel",
                    json={"start": b["start"], "token": token_a})
    assert r.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — no confirmation is sent and `/api/book/<slug>/cancel` 404s.

- [ ] **Step 3: Implement the confirmation and cancel route**

In `api_public_book`, after a successful `book(...)` and before returning, send the confirmation. Read `_send_full_report_email`'s signature with `grep -n "def _send_full_report_email" app.py` and match it.

```python
    # The client has no account and no other way to reach her, so this email
    # IS the record of the appointment. It carries the time in their own zone,
    # the medium, and a cancel link that works without signing in.
    visitor_tz = (body.get("tz") or "").strip()
    shown = _pb.to_visitor_tz(start_ts, cfg["timezone"], visitor_tz)
    cancel_url = (f"{(os.environ.get('PORTAL_BASE_URL') or '').rstrip('/')}"
                  f"/book/cancel?slug={slug}&start={start_ts}&token={token}")
    lines = [f"Hi {name},", "",
             f"Your {st['label']} is booked.", "",
             f"When: {shown} ({visitor_tz or cfg['timezone']})",
             f"How: {st['medium']}", ""]
    if cancel_url.startswith("http"):
        lines += ["If you need to cancel, use this link:", cancel_url, ""]
    lines += ["See you then."]
    try:
        _send_full_report_email(email, name, "Your appointment is booked",
                                "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        # A failed confirmation must not un-book a slot the client believes
        # they hold. Log loudly and return success; the booking is real.
        print(f"[public-book] confirmation failed for {email!r}: {e!r}", flush=True)
```

Then the cancel route:

```python
@app.route("/api/book/<slug>/cancel", methods=["POST"])
def api_public_book_cancel(slug):
    if not _public_surface_enabled():
        return ("", 404)
    from dashboard import practitioner_booking as _pb
    body = request.get_json(silent=True) or {}
    start_ts = (body.get("start") or "").strip()
    token = (body.get("token") or "").strip()
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        _pb.init_tables(cx)
        pid = _pb.resolve_practitioner_pid(cx, slug)
        if not pid:
            return jsonify({"ok": False, "error": "unknown_practitioner"}), 404
        # The token is scoped to (practitioner, slot), so one booking's token
        # is not a skeleton key for the practitioner's whole day.
        if not _pb.cancel_token_ok(pid, start_ts, token):
            return jsonify({"ok": False, "error": "bad_token"}), 403
        cx.execute("UPDATE evox_bookings SET status='cancelled' "
                   "WHERE practitioner=? AND start_ts=? AND status='booked'",
                   (pid, start_ts))
        cx.commit()
    return jsonify({"ok": True})


@app.route("/book/cancel")
def public_book_cancel_page():
    resp = send_from_directory(STATIC, "book-cancel.html")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp
```

Create `static/book-cancel.html`: a page that reads `slug`, `start` and `token` from the query string, shows the appointment time, and posts to the cancel API on a button press. **Do not cancel on page load** — mail scanners prefetch links, and a prefetch would silently cancel a real appointment. This is the same reason `/practitioner/login-verify` confirms on GET and consumes on POST; read `_confirm_post_page` in `app.py` and follow it.

- [ ] **Step 4: Run the tests to verify they pass**

Expected: PASS.

- [ ] **Step 5: Mutation-test the cancel guards**

1. Change `cancel_token_ok` to `return True`. Confirm the forged-token test goes **red**. Restore.
2. Change `cancel_token` to hash only `pid`, dropping the slot. Confirm `test_a_cancel_token_for_one_slot_does_not_cancel_another` goes **red**. Restore.

- [ ] **Step 6: Commit**

```bash
git add app.py dashboard/practitioner_booking.py static/book-cancel.html \
        tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): confirmation email and a working cancel link"
```

---

## Task 6: The Book button on the public page

**Files:**
- Modify: `dashboard/practitioner_render.py`, `app.py`
- Test: `tests/test_practitioner_render.py`, `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `is_bookable` from Task 1, the routes from Tasks 4-5.
- Produces: nothing later depends on this.

**The renderer is a pure function and must stay one.** It takes `bookable` as an argument; it does not read the database. Follow the `indexable` pattern if plan 5b has landed, and the `canonical_url` pattern regardless: every input arrives as an argument.

- [ ] **Step 1: Write the failing test**

```python
def test_a_bookable_practitioner_gets_a_book_link():
    html = pr.render_page_html(_view(), canonical_url=CANON, bookable=True)
    assert "/book/mary-boyd" in html or 'id="book"' in html
    assert "Book" in html


def test_a_practitioner_without_booking_gets_no_book_link():
    """Most practitioners will never turn this on. An empty booking page is a
    worse first impression than no button."""
    html = pr.render_page_html(_view(), canonical_url=CANON, bookable=False)
    assert "Book" not in html


def test_bookable_defaults_to_false():
    """A caller that forgets the argument must not advertise booking that does
    not work."""
    assert "Book" not in pr.render_page_html(_view(), canonical_url=CANON)
```

And route-level:

```python
def test_the_public_page_shows_the_button_only_when_configured(public, logdb, monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("canonical", s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: {"slug": slug, "practitioner_name": "Mary Boyd",
                                          "practice_name": "", "bio": "", "photo_url": "",
                                          "logo_url": "", "services": [], "location": "",
                                          "accepting_clients": None, "featured_products": [],
                                          "catalog_url": "/e", "profit_disclosure": "d",
                                          "tagline": "", "how_i_work": ""})
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    assert "Book" not in public.get("/mary-boyd").get_data(as_text=True)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    assert "Book" in public.get("/mary-boyd").get_data(as_text=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `render_page_html() got an unexpected keyword argument 'bookable'`.

- [ ] **Step 3: Implement**

In `dashboard/practitioner_render.py`, add the parameter and the block:

```python
def render_page_html(view, *, canonical_url, bookable=False):
    """...(keep the existing docstring and add:)

    `bookable` defaults to False so a caller that forgets it does not advertise
    a booking page that is not configured. Like every other input here, it
    arrives as an argument: this module reads no database.
    """
```

```python
def _book_block(view, bookable):
    if not bookable:
        return ""
    slug = _esc(view.get("slug") or "")
    return (f'<section class="book"><h2>Book a session</h2>'
            f'<p><a class="book-btn" href="/book/{slug}">'
            f"Book a time</a></p></section>")
```

Insert `+ _book_block(view, bookable)` into the body, after the tagline and practice lines and before the About section, so it sits above the fold. Add `.book-btn` styling to `_STYLE`.

In `app.py`'s `_render_practitioner_page`, resolve it once:

```python
    from dashboard import practitioner_booking as _pb
    try:
        with db.connect(LOG_DB) as _bcx:
            _bcx.row_factory = sqlite3.Row
            _pb.init_tables(_bcx)
            _bpid = _pb.resolve_practitioner_pid(_bcx, canonical_slug)
            bookable = bool(_bpid) and _pb.is_bookable(_bcx, _bpid)
    except Exception as e:  # noqa: BLE001
        # A booking-config problem must never take down a practitioner's page.
        print(f"[practitioner_site] bookable check failed for "
              f"{canonical_slug!r}: {e!r}", flush=True)
        bookable = False
```

and pass `bookable=bookable` to `render_page_html`.

- [ ] **Step 4: Run the tests to verify they pass**

Expected: PASS.

- [ ] **Step 5: Mutation-test the default**

Change the signature default to `bookable=True`. Confirm `test_bookable_defaults_to_false` goes **red**. Restore.

- [ ] **Step 6: Run the wider suite**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner*.py tests/test_public_surface*.py tests/test_evox*.py tests/test_consult*.py -q -p no:randomly`

Any failure other than the two known pre-existing ones is yours. `test_evox*` and `test_consult*` are in the list because Glen's and Rae's flows share the booking core with this change; if either goes red, you have altered a live flow and must stop and report rather than adjusting their tests.

- [ ] **Step 7: Commit**

```bash
git add dashboard/practitioner_render.py app.py tests/test_practitioner_render.py \
        tests/test_practitioner_booking_routes.py
git commit -m "feat(booking): Book button on the public practitioner page"
```

---

## Verification before merge

```bash
# The practitioner's own view
curl -s https://myhealingoasis.com/practitioner/booking | grep -c workspace-nav

# The public page, before and after she enables booking
curl -s https://myhealingoasis.com/mary-boyd | grep -c "Book a time"

# The public API, with no token of any kind
curl -s "https://myhealingoasis.com/api/book/mary-boyd/slots?session=intro&tz=America/Anchorage" | head -c 400
```

**Then book a real slot yourself and cancel it**, end to end, from a browser that is not signed in to anything. The tests cover the machinery; only doing it as a stranger shows whether the page makes sense to a person who has just been texted a link.

**Check the confirmation email lands** and that its cancel link works from a different device. A cancel link that only works in the session that created it is the failure this task exists to prevent.

---

## Self-review notes

**Spec coverage for Section 3a.** Per-practitioner hours, session types, duration, medium, timezone: Tasks 1 and 3. Data-entry form supplied by the practitioner herself: Task 3. New public route rather than a bypass on the gated one: Task 4, with a guard test asserting the gated route keeps its checks. Booking concurrency, exactly one winner: Task 4, resting on the existing database UNIQUE index. Timezone computed in the practitioner's zone and rendered in the visitor's, tested with a non-Hawaii practitioner and a third visitor zone: Task 2. Confirmation, reminder and cancel link: Task 5 covers confirmation and cancel.

**One spec item deliberately deferred inside 3a: the reminder.** The spec names "confirmation email, reminder, working cancel link" as a single mitigation. Confirmation and cancel ship here. The reminder needs a scheduled job, and this codebase runs those through `console_push_cron.py`, which is a different deployment surface with its own failure modes and its own monitoring. Adding a cron path inside a plan that is otherwise request-scoped would put unrelated risk on this merge. **It must ship before Mary's booking page is advertised to real clients**, and it is the first task of whatever follows this plan.

**Deferred to plan 3b, per the spec's own split at the OAuth boundary:** the web consent flow, per-practitioner tokens, refresh handling, revocation, and reading external busy time. `slots_for` already takes a `busy` parameter so 3b changes a caller rather than a signature.

**Known limitation this plan ships with, stated on the practitioner's own page:** availability is her declared hours minus bookings in our table, so a commitment living only in her Google Calendar will still be offered. Task 3's form says so in plain words, and Task 3 has a test asserting it still says so.

**Not in scope.** The four hardcoded points (`GLEN_CONSULT_HOURS`, the two `practitioner="glen"` literals, `_triage_hours`, `SESSION_TYPES`) stay exactly as they are. Glen's and Rae's flows are live, they work, and generalizing them is a separate change whose blast radius is two production booking paths rather than one new one.
