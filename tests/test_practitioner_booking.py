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
