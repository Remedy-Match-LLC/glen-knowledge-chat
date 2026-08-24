import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard import live_event_series, zoom


def test_series_registration_is_idempotent():
    cx = sqlite3.connect(":memory:")
    live_event_series.upsert_series(
        cx, "group-coaching", "Group Coaching", "123", "https://zoom.test/register")
    live_event_series.set_registration(
        cx, "group-coaching", "Member@Example.com", "reg-1", "https://zoom.test/private")
    live_event_series.set_registration(
        cx, "group-coaching", "member@example.com", "reg-1", "https://zoom.test/private")
    row = live_event_series.get_registration(cx, "group-coaching", "MEMBER@example.com")
    assert row["zoom_join_url"] == "https://zoom.test/private"
    assert cx.execute("SELECT COUNT(*) FROM live_event_series_registrations").fetchone()[0] == 1


def test_occurrence_id_matches_hawaii_start():
    meeting = {"occurrences": [
        {"occurrence_id": "wed", "start_time": "2026-08-27T00:00:00Z"},
        {"occurrence_id": "next", "start_time": "2026-09-03T00:00:00Z"},
    ]}
    target = datetime(2026, 8, 26, 14, 0, tzinfo=ZoneInfo("Pacific/Honolulu"))
    assert zoom.occurrence_id_for(meeting, target) == "wed"
