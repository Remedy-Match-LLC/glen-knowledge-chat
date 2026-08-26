from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.weekly_live_schedule import monday_publish_due


def test_monday_hawaii_is_publish_day():
    assert monday_publish_due(
        datetime(2026, 8, 24, 7, 0, tzinfo=ZoneInfo("Pacific/Honolulu")))


def test_utc_is_converted_before_day_check():
    # 05:00 UTC Tuesday is still Monday evening in Hawai'i.
    assert monday_publish_due(datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc))


def test_other_hawaii_days_do_not_publish():
    assert not monday_publish_due(
        datetime(2026, 8, 26, 7, 0, tzinfo=ZoneInfo("Pacific/Honolulu")))
