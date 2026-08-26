"""Scheduling rules for the Wednesday live-community publication."""

from datetime import datetime
from zoneinfo import ZoneInfo


HST = ZoneInfo("Pacific/Honolulu")


def monday_publish_due(now=None):
    """True during Monday in Hawai'i, when Wednesday's two events are published."""
    current = now or datetime.now(HST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=HST)
    else:
        current = current.astimezone(HST)
    return current.weekday() == 0
