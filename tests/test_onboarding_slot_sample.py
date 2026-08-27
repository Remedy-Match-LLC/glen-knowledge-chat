from unittest import mock

from dashboard import onboarding


def test_daily_slot_sample_randomly_caps_each_day_at_three():
    slots = [
        "2026-08-21T09:00:00",
        "2026-08-21T09:15:00",
        "2026-08-21T09:30:00",
        "2026-08-21T09:45:00",
        "2026-08-22T10:00:00",
        "2026-08-22T10:15:00",
    ]

    with mock.patch.object(
        onboarding.random,
        "sample",
        return_value=[slots[3], slots[0], slots[2]],
    ) as sample:
        result = onboarding.daily_slot_sample(slots)

    sample.assert_called_once_with(slots[:4], 3)
    assert result == [slots[0], slots[2], slots[3], slots[4], slots[5]]


def test_daily_slot_sample_returns_empty_input_unchanged():
    assert onboarding.daily_slot_sample([]) == []
