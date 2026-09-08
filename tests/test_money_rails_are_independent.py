"""One dead payment rail must not take down the whole money summary.

History, because the rule is easier to keep when the reason is in front of you.

Practice Better was deprecated on 2026-09-07 and its OAuth credential died with
it. `today_summary()` and `week_summary()` called `pb_data()` first and
unguarded, so it raised and both summaries died BEFORE Authorize.net was even
attempted. Measured 2026-09-08: /api/money/today and /api/money/week both
returned "400 Client Error ... practicebetter.io/oauth2/token" and no figures.

That also hid the fix shipped in #1589. The Authorize.net rail could not be
observed through either route, because a different rail crashed first.

#1597 made the rails independent. Practice Better itself was then removed, since
a retired system should not be called at all. The rule outlived it and is pinned
here against the rails that remain.

The rule: a rail that could not be read reports `None` and an error, never `0`.
A zero has to keep meaning zero. That is the same defect #1589 fixed inside the
Authorize.net client, one level up.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dashboard import money as _money
except Exception as e:  # pragma: no cover
    pytest.skip(f"money import needs secrets: {e}", allow_module_level=True)


AN_OK = {"total": 50.0, "refunds": 0.0, "net": 50.0, "count": 2,
         "batches": [], "last_success": None}
WISE_OK = {"balances": [{"currency": "USD", "amount": 10}]}


def _boom(*a, **k):
    raise RuntimeError("400 Client Error: Bad Request for url: .../oauth2/token")


# ── Practice Better is gone, not merely guarded ──────────────────────────────

def test_practice_better_is_no_longer_called_at_all():
    """Guarding a dead rail still calls it on every page load. It was retired on
    2026-09-08, so the call should not exist."""
    assert not hasattr(_money, "pb_data")
    assert not hasattr(_money, "pb_token")
    src = (Path(__file__).resolve().parent.parent / "dashboard" / "money.py").read_text()
    assert "api.practicebetter.io" not in src


# ── A dead rail must not kill the others ─────────────────────────────────────

def test_today_summary_survives_a_dead_authorize_net(monkeypatch):
    monkeypatch.setattr(_money, "an_data", _boom)
    monkeypatch.setattr(_money, "wise_data", lambda: WISE_OK)

    out = _money.today_summary()

    assert out["wise_balances"] == WISE_OK["balances"], out
    assert "authorize_net" in (out.get("errors") or {}), out


def test_a_dead_rail_reports_none_not_zero(monkeypatch):
    """A zero here would read as 'no money came in', which is a lie."""
    monkeypatch.setattr(_money, "an_data", _boom)
    monkeypatch.setattr(_money, "wise_data", lambda: WISE_OK)

    today = _money.today_summary()
    assert today["an_today"] is None, f"a dead rail reported {today['an_today']!r}"

    week = _money.week_summary()
    assert week["an_net"] is None, f"a dead rail reported {week['an_net']!r}"
    assert week["an_count"] is None, week
    assert "authorize_net" in (week.get("errors") or {}), week


def test_wise_can_die_without_taking_authorize_net_with_it(monkeypatch):
    monkeypatch.setattr(_money, "an_data", lambda days=1: AN_OK)
    monkeypatch.setattr(_money, "wise_data", _boom)

    out = _money.today_summary()

    assert out["wise_balances"] is None, out
    assert out["an_today"] == 0, "the healthy rail lost its figure"
    assert "wise" in (out.get("errors") or {}), out


def test_all_rails_healthy_reports_no_errors(monkeypatch):
    monkeypatch.setattr(_money, "an_data", lambda days=1: AN_OK)
    monkeypatch.setattr(_money, "wise_data", lambda: WISE_OK)

    out = _money.today_summary()

    assert not out.get("errors"), out
    assert out["an_today"] == 0, "a genuine empty day is still 0, not None"


def test_a_genuine_zero_is_still_zero(monkeypatch):
    """The whole point: 0 must keep meaning 0, distinct from None."""
    monkeypatch.setattr(_money, "an_data", lambda days=7: dict(AN_OK, net=0.0, count=0))

    out = _money.week_summary()

    assert out["an_net"] == 0.0, out
    assert out["an_count"] == 0, out
    assert not out.get("errors"), out
