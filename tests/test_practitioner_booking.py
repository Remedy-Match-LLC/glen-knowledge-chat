"""Per-practitioner booking configuration.

A config we cannot parse must offer NO slots rather than fall back to someone
else's hours. Offering Glen's Hawaii hours to Mary's Alaska clients would put
real people on a call at the wrong time, which is worse than an empty page.
"""
import json
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


def test_a_missing_timezone_key_is_rejected():
    """DEFAULT_TIMEZONE is a form pre-fill suggestion, not a validator
    fallback. An omitted key must never silently become Hawaii."""
    cfg = _cfg()
    del cfg["timezone"]
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(cfg)


def test_a_blank_timezone_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(timezone=""))


@pytest.mark.parametrize("bad", [
    "Etc/GMT+9", "Etc/GMT-9", "Etc/UTC",
    "etc/GMT+9",   # lowercase prefix -- ZoneInfo resolves it same as Etc/
    "ETC/GMT+9",   # uppercase prefix -- same
])
def test_an_etc_gmt_offset_is_rejected(bad):
    """Etc/GMT+9 contains a slash and resolves cleanly through ZoneInfo, so
    the plain 'no slash' check walks right past it. It is the same
    fixed-offset bug in a different spelling, and its sign is backwards:
    Etc/GMT+9 means UTC minus 9, not plus 9. The rejection must also be
    case-insensitive: on at least some platforms ZoneInfo resolves
    'etc/GMT+9' and 'ETC/GMT+9' exactly the same as 'Etc/GMT+9', so a
    case-sensitive startswith('Etc/') is a one-character-case bypass."""
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(timezone=bad))


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


@pytest.mark.parametrize("reserved", ["biofield-consult", "onboarding", "triage"])
def test_a_reserved_session_type_slug_is_rejected(reserved):
    """These three literals are branched on, unfiltered by practitioner, by
    existing production code: app.py's /api/consult/join hands back
    GLEN_PMI_URL (Glen's personal Zoom meeting) to whoever matches
    session_type='biofield-consult', and the reminder cron / portal calendar
    both key wording/labels off 'onboarding' and 'triage'. A practitioner
    naming her own session type one of these collides with a flow that is
    not hers -- in the biofield-consult case, handing her client Glen's
    personal meeting URL. Reject at the door with a message that tells her
    to pick another name, not a regex complaint."""
    with pytest.raises(pb.BookingConfigError) as exc:
        pb.validate_config(_cfg(session_types=[
            {"slug": reserved, "label": "Whatever", "duration_min": 20,
             "medium": "phone"}]))
    assert reserved in str(exc.value)
    assert "pick" in str(exc.value).lower() or "different" in str(exc.value).lower()


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


def _insert_row(cx, timezone="America/Anchorage", office_hours="1-5:09:00-17:00",
                 session_types='[{"slug": "intro", "label": "Intro", '
                                '"duration_min": 20, "medium": "phone"}]'):
    """Write a row directly, bypassing set_config's validation -- the way a
    hand edit, a future migration, or a partial write actually could."""
    cx.execute("INSERT INTO practitioner_booking_config (practitioner_id, "
               "timezone, office_hours, session_types, notice_hours, "
               "buffer_min, enabled, updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (PID, timezone, office_hours, session_types,
                24, 0, 1, "2026-01-01T00:00:00+00:00"))
    cx.commit()


def test_corrupt_stored_json_is_not_bookable(cx):
    """A row that fails to parse must offer NOTHING, not somebody else's
    default hours. None of the tests above write malformed JSON directly, so
    this exercises the fail-closed branch in get_config that they don't
    reach: the practitioner_booking_config table is written to directly,
    bypassing set_config's validation."""
    _insert_row(cx, session_types="{not json")
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_corrupt_stored_office_hours_is_not_bookable(cx):
    """get_config's own justification for guarding session_types JSON --
    hand-edited data, a future migration, a partial write -- applies just as
    much to office_hours. A row this module cannot correctly interpret must
    not be treated as bookable."""
    _insert_row(cx, office_hours="garbage")
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_corrupt_stored_timezone_is_not_bookable(cx):
    _insert_row(cx, timezone="UTC-9")
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_a_second_save_replaces_rather_than_duplicates(cx):
    pb.set_config(cx, PID, _cfg())
    pb.set_config(cx, PID, _cfg(office_hours="2-4:10:00-14:00"))
    assert pb.get_config(cx, PID)["office_hours"] == "2-4:10:00-14:00"
    rows = cx.execute("SELECT COUNT(*) c FROM practitioner_booking_config "
                      "WHERE practitioner_id=?", (PID,)).fetchone()
    assert rows["c"] == 1


def test_a_second_save_replaces_notify_methods_too(cx):
    """The upsert is structurally the same for every column, but that has
    been wrong before in this task -- assert it explicitly for the new one
    rather than assume it rides along with office_hours's coverage."""
    pb.set_config(cx, PID, _cfg(notify_methods=["phone", "text"],
                                phone="+1 907-555-0100"))
    pb.set_config(cx, PID, _cfg(notify_methods=["calendar"]))
    assert pb.get_config(cx, PID)["notify_methods"] == ["calendar"]
    rows = cx.execute("SELECT COUNT(*) c FROM practitioner_booking_config "
                      "WHERE practitioner_id=?", (PID,)).fetchone()
    assert rows["c"] == 1


def test_notify_methods_round_trip(cx):
    pb.set_config(cx, PID, _cfg(notify_methods=["email", "text"],
                                phone="+1 907-555-0100"))
    assert pb.get_config(cx, PID)["notify_methods"] == ["email", "text"]


def test_notify_methods_defaults_to_email_only():
    """A practitioner who never touches this keeps exactly today's behaviour.
    It must NOT default to every method: publishing her phone number because
    she left a box alone is the system choosing on her behalf."""
    out = pb.validate_config(_cfg())
    assert out["notify_methods"] == ["email"]


def test_every_method_may_be_chosen():
    out = pb.validate_config(_cfg(notify_methods=["phone", "text", "email", "calendar"]))
    assert set(out["notify_methods"]) == {"phone", "text", "email", "calendar"}


def test_an_unknown_method_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(notify_methods=["carrier-pigeon"]))


def test_duplicates_are_collapsed_not_rejected():
    """A checkbox UI can submit the same value twice. That is not the
    practitioner's mistake and should not be an error she has to fix."""
    out = pb.validate_config(_cfg(notify_methods=["email", "email", "text"]))
    assert out["notify_methods"] == ["email", "text"]


def test_an_empty_list_is_rejected():
    """Choosing nothing means she never hears about a booking at all. That is
    almost certainly a mis-click, and the cost of guessing wrong is she misses
    an appointment."""
    with pytest.raises(pb.BookingConfigError) as e:
        pb.validate_config(_cfg(notify_methods=[]))
    assert "at least one" in str(e.value).lower()


def test_a_string_instead_of_a_list_is_rejected():
    with pytest.raises(pb.BookingConfigError):
        pb.validate_config(_cfg(notify_methods="email"))


def test_a_stored_row_with_unparseable_notify_methods_fails_closed(cx):
    """get_config re-validates on read. A row whose methods are not even
    valid JSON must return None like every other unreadable field, not a
    half-config. This exercises the JSON-syntax guard (the same try/except
    that already catches a corrupt session_types blob) -- it does NOT prove
    the semantic guard (_validate_notify_methods) runs, because invalid JSON
    never reaches that call either way."""
    pb.set_config(cx, PID, _cfg())
    cx.execute("UPDATE practitioner_booking_config SET notify_methods=? "
               "WHERE practitioner_id=?", ("{not json", PID))
    cx.commit()
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_a_stored_row_with_unknown_notify_method_fails_closed(cx):
    """Syntactically valid JSON, semantically invalid: a method that is not
    one of NOTIFY_METHODS. This can only be caught by re-running the stored
    value through _validate_notify_methods on read -- the JSON parser alone
    would happily accept it. If this guard were removed, this row would
    load with an unknown method still inside it."""
    pb.set_config(cx, PID, _cfg())
    cx.execute("UPDATE practitioner_booking_config SET notify_methods=? "
               "WHERE practitioner_id=?", ('["carrier-pigeon"]', PID))
    cx.commit()
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_a_stored_row_with_a_bare_string_notify_methods_fails_closed(cx):
    """Syntactically valid JSON, wrong type: a bare string instead of a
    list. A hand-edited row or a future migration could plausibly produce
    this shape. Only the semantic guard rejects it -- JSON parsing alone
    succeeds and hands back a Python str."""
    pb.set_config(cx, PID, _cfg())
    cx.execute("UPDATE practitioner_booking_config SET notify_methods=? "
               "WHERE practitioner_id=?", (json.dumps("email"), PID))
    cx.commit()
    assert pb.get_config(cx, PID) is None
    assert pb.is_bookable(cx, PID) is False


def test_a_null_notify_methods_column_defaults_to_email_and_stays_bookable(cx):
    """A row written before this column existed has NULL there, not a
    corrupt value. That must resolve to the default (['email']) and the
    row must otherwise stay perfectly usable -- unlike every other test in
    this file that touches a NULL/corrupt notify_methods, this one starts
    from a config that is valid in EVERY other respect, so the only thing
    under test is the NULL -> default path itself. All practitioners with a
    config saved before this feature shipped are in exactly this shape."""
    pb.set_config(cx, PID, _cfg())
    cx.execute("UPDATE practitioner_booking_config SET notify_methods=NULL "
               "WHERE practitioner_id=?", (PID,))
    cx.commit()
    got = pb.get_config(cx, PID)
    assert got is not None
    assert got["notify_methods"] == ["email"]
    assert pb.is_bookable(cx, PID) is True


from datetime import date, datetime


def test_now_in_matches_the_zone_database_for_several_zones():
    """A hardcoded offset (the _hst_now bug) passes a naivety check and fails
    this one. Compares against an independently computed value rather than
    against another call to the function under test."""
    from datetime import datetime, timezone as _tzmod
    from zoneinfo import ZoneInfo
    utc = datetime.now(_tzmod.utc)
    for tz in ("America/Anchorage", "Pacific/Honolulu", "Europe/London",
               "Australia/Sydney"):
        expected = utc.astimezone(ZoneInfo(tz)).replace(tzinfo=None)
        got = pb.now_in(tz)
        assert abs((got - expected).total_seconds()) < 5, (
            f"{tz}: got {got}, zone database says {expected}")


def test_now_in_reflects_the_real_offset_between_two_zones():
    """One hardcoded offset cannot be right for two places at once.

    Compares the DIFFERENCE between two zones against the difference the zone
    database gives for the same instant. An earlier version of this test just
    asserted the two results were unequal, which passed on the microseconds
    of real time between the two now() calls -- green whether or not the
    function looked at its argument at all.
    """
    from datetime import datetime, timezone as _tzmod
    from zoneinfo import ZoneInfo
    utc = datetime.now(_tzmod.utc)
    a, h = "America/Anchorage", "Pacific/Honolulu"
    expected = (utc.astimezone(ZoneInfo(a)).replace(tzinfo=None)
                - utc.astimezone(ZoneInfo(h)).replace(tzinfo=None))
    got = pb.now_in(a) - pb.now_in(h)
    assert abs((got - expected).total_seconds()) < 5, (
        f"expected about {expected} between {a} and {h}, got {got}")


def test_now_in_returns_naive_datetimes():
    """Kept separately from the offset tests: evox.available_slots compares
    against a naive grid and mixing aware with naive raises."""
    assert pb.now_in("America/Anchorage").tzinfo is None


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin pb.now_in far enough before the pinned `date(2026, 9, 7)` calendar
    dates below that the notice_hours cutoff (slots_for adds notice_hours to
    now_in's result and drops anything earlier) never trims the office-hours
    window under test, regardless of what today's real date is.

    Without this, slots_for's cutoff is derived from the REAL clock
    (dashboard.practitioner_booking.now_in -> datetime.now(timezone.utc)), so
    a test pinning a calendar date goes red the moment real "now" gets within
    notice_hours of that pinned date and stays red forever after -- a date
    bomb. Freezing now_in instead of the calendar date is what the plan
    calls for: the pinned date stays a fixed, arbitrary point in the future
    relative to a fixed, arbitrary "now", so this is stable on every future
    run date, not just until 2026-09-06.
    """
    fixed = datetime(2026, 8, 20, 0, 0, 0)
    monkeypatch.setattr(pb, "now_in", lambda tz_name: fixed)
    return fixed


def test_slots_come_back_in_the_practitioner_timezone(cx, frozen_now):
    pb.set_config(cx, PID, _cfg(office_hours="1-5:09:00-17:00"))
    days = [date(2026, 9, 7)]              # a Monday
    got = pb.slots_for(cx, PID, days=days, session_slug="intro", booked=set())
    assert got, "expected slots on a weekday inside office hours"
    assert all(s.startswith("2026-09-07T") for s in got)
    assert got[0] == "2026-09-07T09:00:00"


def test_slot_length_follows_the_session_type(cx, frozen_now):
    pb.set_config(cx, PID, _cfg(session_types=[
        {"slug": "intro", "label": "Intro", "duration_min": 20, "medium": "phone"},
        {"slug": "full", "label": "Full", "duration_min": 60, "medium": "zoom"}]))
    days = [date(2026, 9, 7)]
    short = pb.slots_for(cx, PID, days=days, session_slug="intro", booked=set())
    long = pb.slots_for(cx, PID, days=days, session_slug="full", booked=set())
    assert len(short) > len(long)
    assert short[1] == "2026-09-07T09:20:00"
    assert long[1] == "2026-09-07T10:00:00"


def test_an_unknown_session_type_offers_nothing(cx, frozen_now):
    pb.set_config(cx, PID, _cfg())
    assert pb.slots_for(cx, PID, days=[date(2026, 9, 7)],
                        session_slug="nope", booked=set()) == []


def test_a_practitioner_with_no_config_offers_nothing(cx, frozen_now):
    assert pb.slots_for(cx, "pid-nobody", days=[date(2026, 9, 7)],
                        session_slug="intro", booked=set()) == []


def test_a_booked_slot_is_not_offered(cx, frozen_now):
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


def test_a_none_visitor_timezone_falls_back_to_the_practitioner():
    """ZoneInfo(None) raises TypeError, not one of (ZoneInfoNotFoundError,
    ValueError, KeyError). A visitor's browser that fails to report a
    timezone at all is just as unusable as one reporting garbage, and the
    promise is the same: fall back, never 500."""
    out = pb.to_visitor_tz("2026-09-07T09:00:00", "America/Anchorage", None)
    assert out.startswith("2026-09-07T09:00")


def test_a_none_practitioner_timezone_falls_back_to_the_default():
    """Same TypeError trap on the practitioner side. Not expected to be
    reachable through get_config (it always returns a validated string), but
    to_visitor_tz must not raise if it is ever called with one directly."""
    out = pb.to_visitor_tz("2026-09-07T09:00:00", None, "UTC")
    assert out.startswith("2026-09-07T19:00:00")


def test_now_in_with_a_none_timezone_falls_back_to_the_default():
    """now_in has the same ZoneInfo(None) trap; it is a produced interface a
    future caller could reach with unvalidated input."""
    n = pb.now_in(None)
    assert n.tzinfo is None


# ── the booking phone ──────────────────────────────────────────────────────
#
# This number lives on practitioner_booking_config, NOT on practitioners.phone.
# They are two different facts: "the number my booking clients call me on" and
# "my public directory number", and they may legitimately differ. Conflating
# them published a booking number through v_practitioners_public ->
# /api/practitioner-finder/search, which is public and unauthenticated, for
# every portal practitioner (tier 'org_member' is outside the redaction set
# that route applies).
#
# validate_phone is deliberately permissive: the number is DISPLAYED for a
# human to dial, never parsed, so a validator that rejects a legitimate
# Alaskan or Auckland number is worse than one that accepts an odd-looking
# one. These tests pin exactly that permissiveness so a later "tidy-up"
# cannot quietly narrow it.

def test_validate_phone_accepts_plain_digits_and_common_punctuation():
    for raw in ["+1 555-0100", "(907) 555-0100", "907.555.0100", "15550100",
                "+64 (0)9 555 0100"]:
        clean, err = pb.validate_phone(raw)
        assert err is None, f"{raw!r} should be accepted, got error: {err}"
        assert clean == raw.strip()


def test_validate_phone_empty_clears_it():
    """An empty submission is not an error -- it is how she removes a number
    she previously saved."""
    assert pb.validate_phone("") == ("", None)
    assert pb.validate_phone("   ") == ("", None)
    assert pb.validate_phone(None) == ("", None)


def test_validate_phone_rejects_too_few_digits():
    clean, err = pb.validate_phone("+1-2-3")
    assert clean is None
    assert err


def test_validate_phone_rejects_letters():
    clean, err = pb.validate_phone("call me maybe")
    assert clean is None
    assert err


def test_validate_phone_rejects_too_long():
    clean, err = pb.validate_phone("1" * 40)
    assert clean is None
    assert err


def test_the_phone_number_round_trips_on_the_config_row(cx):
    """Stored beside notify_methods, read back off the same row. No Supabase
    lookup, no practitioners.phone."""
    pb.set_config(cx, PID, _cfg(notify_methods=["phone"], phone="+1 907-555-0100"))
    assert pb.get_config(cx, PID)["phone"] == "+1 907-555-0100"
    stored = cx.execute("SELECT phone FROM practitioner_booking_config "
                        "WHERE practitioner_id=?", (PID,)).fetchone()
    assert stored["phone"] == "+1 907-555-0100"


def test_a_config_with_no_phone_reads_back_as_empty_string(cx):
    """Never None, and never a number borrowed from somewhere else."""
    pb.set_config(cx, PID, _cfg())
    assert pb.get_config(cx, PID)["phone"] == ""


def test_a_save_that_omits_phone_keeps_the_saved_number(cx):
    """The Critical-2 trap in its data form. A config save that does not
    carry the field at all must leave the stored number alone -- collapsing
    "not supplied" into "clear it" is what let one blank form wipe a real
    number under a "Saved." message."""
    pb.set_config(cx, PID, _cfg(notify_methods=["text"], phone="+1 907-555-0100"))
    pb.set_config(cx, PID, _cfg(notify_methods=["text"], office_hours="2-4:10:00-14:00"))
    got = pb.get_config(cx, PID)
    assert got["phone"] == "+1 907-555-0100"
    assert got["office_hours"] == "2-4:10:00-14:00", "the rest of the save still applied"


def test_clearing_the_number_is_still_possible_as_a_deliberate_action(cx):
    """An explicitly empty phone with no method that needs one clears it.
    The bug was a SILENT clear after a failed load, not clearing itself."""
    pb.set_config(cx, PID, _cfg(notify_methods=["text"], phone="+1 907-555-0100"))
    pb.set_config(cx, PID, _cfg(notify_methods=["email"], phone=""))
    assert pb.get_config(cx, PID)["phone"] == ""


def test_an_unusable_phone_number_is_rejected_by_set_config(cx):
    with pytest.raises(pb.BookingConfigError):
        pb.set_config(cx, PID, _cfg(phone="call me maybe"))
    assert pb.get_config(cx, PID) is None, "a rejected save must write nothing"


@pytest.mark.parametrize("methods", [["phone"], ["text"], ["email", "text"],
                                     ["phone", "text"]])
def test_choosing_phone_or_text_without_a_number_is_refused(cx, methods):
    """The High. Nothing server-side used to require a number, so ["phone"]
    with none saved told NEITHER party anything: no Call: line for the client,
    and the practitioner block deliberately sends her nothing for "phone".
    ["text"] handed GHL an empty number, which it declined, and the reason was
    discarded at the call site. Refuse the combination at the writer."""
    with pytest.raises(pb.BookingConfigError) as e:
        pb.set_config(cx, PID, _cfg(notify_methods=methods))
    msg = str(e.value)
    assert "phone number" in msg.lower(), "the message must say what to do"
    assert pb.get_config(cx, PID) is None, "a refused save must write nothing"


def test_the_number_may_arrive_in_the_same_save_as_the_method(cx):
    """Why the phone had to become part of this one atomic save: she ticks
    "text" and types her number in a single action, so the guard above must
    accept a number supplied in the SAME request rather than demanding one
    already be on file."""
    clean = pb.set_config(cx, PID, _cfg(notify_methods=["text"],
                                        phone="+1 907-555-0100"))
    assert clean["phone"] == "+1 907-555-0100"
    assert pb.get_config(cx, PID)["notify_methods"] == ["text"]


def test_an_already_saved_number_satisfies_a_later_method_change(cx):
    """She saved a number last week; today she ticks "phone" and the form
    posts the field unchanged or not at all. Either way the guard is
    satisfied by what is already stored."""
    pb.set_config(cx, PID, _cfg(notify_methods=["text"], phone="+1 907-555-0100"))
    clean = pb.set_config(cx, PID, _cfg(notify_methods=["phone", "text"]))
    assert clean["phone"] == "+1 907-555-0100"


def test_clearing_the_number_while_phone_is_still_ticked_is_refused(cx):
    """The other direction of the same guard: removing the number without
    also un-ticking the method would leave her page advertising a way to
    reach her that resolves to nothing."""
    pb.set_config(cx, PID, _cfg(notify_methods=["phone"], phone="+1 907-555-0100"))
    with pytest.raises(pb.BookingConfigError):
        pb.set_config(cx, PID, _cfg(notify_methods=["phone"], phone=""))
    assert pb.get_config(cx, PID)["phone"] == "+1 907-555-0100", \
        "the refused save must not have partially applied"


def test_email_and_calendar_never_require_a_number(cx):
    """Only the two methods that actually consume one are gated. A guard that
    refused every method would lock out the practitioners this feature was
    already working for."""
    clean = pb.set_config(cx, PID, _cfg(notify_methods=["email", "calendar"]))
    assert clean["phone"] == ""


def test_the_booking_config_never_reaches_for_the_directory_number(cx, monkeypatch):
    """No fallback, anywhere. Reading practitioners.phone in as a prefill
    would publish her directory number on her public page the moment she
    ticked "phone", without her ever having typed it.

    Proved by behaviour rather than by grepping the source: Supabase -- the
    only place practitioners.phone lives -- is made to explode, and the whole
    read/write cycle still works. A fallback would have to go through it.
    """
    import db_supabase

    def boom(*a, **kw):
        raise AssertionError("the booking config reached for Supabase")
    monkeypatch.setattr(db_supabase, "supabase_cursor", boom)

    pb.set_config(cx, PID, _cfg(notify_methods=["phone"], phone="+1 907-555-0100"))
    assert pb.get_config(cx, PID)["phone"] == "+1 907-555-0100"
    # And with nothing typed there is nothing to publish -- not a number
    # borrowed from the directory.
    pb.set_config(cx, PID, _cfg(notify_methods=["email"], phone=""))
    assert pb.get_config(cx, PID)["phone"] == ""


def test_the_old_directory_phone_getter_is_gone():
    """practitioner_phone_by_id read practitioners.phone -- the column
    v_practitioners_public publishes through the unauthenticated
    /api/practitioner-finder/search. It had no caller left once the booking
    phone moved, and leaving a live reader of that column in place invites a
    future caller to re-conflate the two facts."""
    from dashboard import practitioner_portal as pp
    assert not hasattr(pp, "practitioner_phone_by_id")
    assert not hasattr(pp, "set_practitioner_phone")
