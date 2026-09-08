"""A dead payment source must not take the money dashboard down, and must not
be reported as a zero either.

Two failures, one week apart, in opposite directions.

Authorize.net's caller read `batchList` off a rejected response and reported
$0.00, so a dead credential looked like a quiet week for months. `an_post` now
raises, which is right.

Practice Better was retired and its credentials revoked, but `pb_data` was still
called by both summaries. `@cached` re-raises when it holds no stale value, so
the dead credential returned HTTP 500 from `/api/money/today` and
`/api/money/week`. The money dashboard was unreachable while the health grid
reported green around it.

So each source is read independently. A failure yields None, never 0.0, and is
named in `unavailable`.
"""
import pytest

from dashboard import money


def test_practice_better_is_gone():
    assert not hasattr(money, "pb_data")
    assert not hasattr(money, "pb_token")


def test_a_dead_source_does_not_take_the_summary_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Authorize.net rejected the request: E00007")
    monkeypatch.setattr(money, "an_data", boom)
    monkeypatch.setattr(money, "wise_data", lambda *a, **k: {"balances": [{"currency": "USD", "amount": 1}]})

    d = money.today_summary()
    assert d["wise_balances"] == [{"currency": "USD", "amount": 1}]
    assert d["unavailable"] and "authorize_net" in d["unavailable"][0]


def test_a_dead_source_is_never_reported_as_zero(monkeypatch):
    """The whole point. A zero is a claim that no money moved."""
    def boom(*a, **k):
        raise RuntimeError("E00007")
    monkeypatch.setattr(money, "an_data", boom)
    monkeypatch.setattr(money, "wise_data", lambda *a, **k: {"balances": []})

    today = money.today_summary()
    assert today["an_today"] is None
    assert today["an_today"] != 0
    assert today["an_today"] != 0.0

    week = money.week_summary()
    assert week["an_net"] is None
    assert week["an_count"] is None


def test_every_dead_source_is_named(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(money, "an_data", boom)
    monkeypatch.setattr(money, "wise_data", boom)
    d = money.today_summary()
    joined = " ".join(d["unavailable"])
    assert "authorize_net" in joined and "wise" in joined
    assert d["wise_balances"] is None


def test_a_healthy_summary_reports_numbers_and_nothing_unavailable(monkeypatch):
    monkeypatch.setattr(money, "an_data", lambda days=1: {
        "batches": [], "net": 12.5, "count": 3})
    monkeypatch.setattr(money, "wise_data", lambda *a, **k: {"balances": []})
    assert money.today_summary()["unavailable"] == []
    w = money.week_summary()
    assert w["an_net"] == 12.5 and w["an_count"] == 3 and w["unavailable"] == []


def test_the_error_text_is_truncated_not_dumped(monkeypatch):
    """A provider can answer with an HTML error page. The card shows this."""
    def boom(*a, **k):
        raise RuntimeError("x" * 5000)
    monkeypatch.setattr(money, "an_data", boom)
    monkeypatch.setattr(money, "wise_data", lambda *a, **k: {"balances": []})
    assert len(money.today_summary()["unavailable"][0]) < 300
