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


@pytest.mark.parametrize("bad", ["Etc/GMT+9", "Etc/GMT-9", "Etc/UTC"])
def test_an_etc_gmt_offset_is_rejected(bad):
    """Etc/GMT+9 contains a slash and resolves cleanly through ZoneInfo, so
    the plain 'no slash' check walks right past it. It is the same
    fixed-offset bug in a different spelling, and its sign is backwards:
    Etc/GMT+9 means UTC minus 9, not plus 9."""
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
