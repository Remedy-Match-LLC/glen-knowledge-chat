"""parse_utc must read every timestamp shape that is live in auth_tokens."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

repo = Path(__file__).resolve().parent.parent
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))

from dashboard.timeutil import parse_utc, is_expired, now_utc, format_ttl

# Exactly the shapes observed in the production table, produced by minting one
# token per purpose through the real code.
AWARE = "2026-07-09T17:49:00.002684+00:00"   # _now_utc().isoformat()
Z_NAIVE = "2026-08-08T17:33:59.998605Z"      # datetime.utcnow().isoformat() + "Z"
BARE_NAIVE = "2027-07-09T17:33:59.999677"    # datetime.utcnow().isoformat()
AWARE_PLUS_Z = "2027-07-09T17:33:59.999677+00:00Z"  # isoformat() + "Z" on an aware dt


@pytest.mark.parametrize("text", [AWARE, Z_NAIVE, BARE_NAIVE, AWARE_PLUS_Z])
def test_every_stored_shape_parses_to_aware_utc(text):
    dt = parse_utc(text)
    assert dt.tzinfo is not None, "result must be aware or comparisons still raise"
    assert dt.utcoffset() == timedelta(0)


def test_the_three_shapes_of_one_instant_compare_equal():
    """A naive value is UTC, because every minter writing one uses utcnow()."""
    stamp = "2026-07-09T17:33:59.999677"
    assert parse_utc(stamp) == parse_utc(stamp + "Z") == parse_utc(stamp + "+00:00")


def test_comparison_across_shapes_never_raises():
    """The whole bug: naive vs aware raised TypeError, and a bare except turned
    that into 'expired'."""
    assert parse_utc(BARE_NAIVE) > parse_utc(Z_NAIVE)
    assert parse_utc(AWARE) < parse_utc(BARE_NAIVE)


def test_naive_datetime_object_is_treated_as_utc():
    naive = datetime(2026, 7, 9, 12, 0, 0)
    assert parse_utc(naive) == datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["", None, "not-a-date", "  "])
def test_unparseable_raises(bad):
    with pytest.raises(ValueError):
        parse_utc(bad)


def test_is_expired_reads_each_shape():
    past = (now_utc() - timedelta(hours=1)).replace(tzinfo=None)
    future = (now_utc() + timedelta(hours=1)).replace(tzinfo=None)
    for fmt in (lambda d: d.isoformat(),
                lambda d: d.isoformat() + "Z",
                lambda d: d.replace(tzinfo=timezone.utc).isoformat()):
        assert is_expired(fmt(past)) is True
        assert is_expired(fmt(future)) is False


def test_is_expired_treats_garbage_as_expired():
    """A token whose expiry we cannot read must not be honoured."""
    assert is_expired("not-a-date") is True
    assert is_expired(None) is True


def test_is_expired_accepts_an_explicit_now_in_either_shape():
    exp = "2026-07-09T12:00:00Z"
    assert is_expired(exp, now=datetime(2026, 7, 9, 13, 0, 0)) is True           # naive
    assert is_expired(exp, now=datetime(2026, 7, 9, 11, 0, 0,
                                        tzinfo=timezone.utc)) is False           # aware


# ── format_ttl ───────────────────────────────────────────────────────────────
# Renders a minutes value as expiry copy. Picks the coarsest unit that divides
# the value evenly rather than rounding, so the words never overstate or
# understate the real lifetime a person was promised.

def test_exactly_a_week():
    assert format_ttl(7 * 24 * 60) == "a week"


def test_more_than_a_week_in_exact_weeks():
    assert format_ttl(14 * 24 * 60) == "2 weeks"


def test_more_than_a_week_not_an_exact_week_falls_back_to_days():
    assert format_ttl(8 * 24 * 60) == "8 days"


def test_a_day():
    assert format_ttl(24 * 60) == "a day"


def test_multiple_days():
    assert format_ttl(3 * 24 * 60) == "3 days"


def test_an_hour():
    assert format_ttl(60) == "an hour"


def test_multiple_hours():
    assert format_ttl(3 * 60) == "3 hours"


def test_under_an_hour():
    assert format_ttl(30) == "30 minutes"


def test_a_single_minute():
    assert format_ttl(1) == "1 minute"


def test_value_that_does_not_divide_evenly():
    """100 minutes is not a whole number of hours (100 % 60 != 0), so it must
    stay in minutes rather than being rounded into a misleading '2 hours'."""
    assert format_ttl(100) == "100 minutes"
