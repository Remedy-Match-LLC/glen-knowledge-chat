"""One dead payment rail must not take down the whole money summary.

Practice Better was deprecated on 2026-09-07 and its OAuth credential is dead.
`today_summary()` and `week_summary()` called `pb_data()` first and unguarded,
so it raised and both summaries died BEFORE Authorize.net was even attempted.
Measured 2026-09-08: /api/money/today and /api/money/week both returned
"400 Client Error ... practicebetter.io/oauth2/token" and no figures at all.

That also hid the fix shipped in #1589. The Authorize.net rail could not be
observed through either route, because a different rail crashed first.

The rule this file pins: a rail that could not be read reports `None` and an
error, never `0`. A zero has to keep meaning zero. That is the same defect
#1589 fixed inside the Authorize.net client, one level up.
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
PB_OK = {"invoices": [], "collected": 12.0, "outstanding": 3.0}


def _boom(*a, **k):
    raise RuntimeError("400 Client Error: Bad Request for url: .../oauth2/token")


# ── A dead rail must not kill the others ─────────────────────────────────────

def test_week_summary_survives_a_dead_practice_better(monkeypatch):
    monkeypatch.setattr(_money, "pb_data", _boom)
    monkeypatch.setattr(_money, "an_data", lambda days=7: AN_OK)

    out = _money.week_summary()

    assert out["an_net"] == 50.0, out
    assert out["an_count"] == 2, out


def test_week_summary_reports_the_dead_rail_as_none_not_zero(monkeypatch):
    """A zero here would read as 'no money came in', which is a lie."""
    monkeypatch.setattr(_money, "pb_data", _boom)
    monkeypatch.setattr(_money, "an_data", lambda days=7: AN_OK)

    out = _money.week_summary()

    assert out["pb_collected"] is None, f"a dead rail reported {out['pb_collected']!r}"
    assert out["pb_outstanding"] is None, out
    assert "practice_better" in (out.get("errors") or {}), out


def test_today_summary_survives_a_dead_authorize_net(monkeypatch):
    """The rail that is actually broken today. It must not zero out either."""
    monkeypatch.setattr(_money, "pb_data", lambda days=1: PB_OK)
    monkeypatch.setattr(_money, "an_data", _boom)
    monkeypatch.setattr(_money, "wise_data", lambda: {"balances": []})

    out = _money.today_summary()

    assert out["an_today"] is None, f"a dead rail reported {out['an_today']!r}"
    assert "authorize_net" in (out.get("errors") or {}), out


def test_all_rails_healthy_reports_no_errors(monkeypatch):
    monkeypatch.setattr(_money, "pb_data", lambda days=1: PB_OK)
    monkeypatch.setattr(_money, "an_data", lambda days=1: AN_OK)
    monkeypatch.setattr(_money, "wise_data", lambda: {"balances": []})

    out = _money.today_summary()

    assert not out.get("errors"), out
    assert out["an_today"] == 0, "a genuine empty day is still 0, not None"


def test_a_genuine_zero_is_still_zero(monkeypatch):
    """The whole point: 0 must keep meaning 0, distinct from None."""
    monkeypatch.setattr(_money, "pb_data", lambda days=7: {"invoices": [], "collected": 0.0, "outstanding": 0.0})
    monkeypatch.setattr(_money, "an_data", lambda days=7: dict(AN_OK, net=0.0, count=0))

    out = _money.week_summary()

    assert out["an_net"] == 0.0, out
    assert out["pb_collected"] == 0.0, out
    assert not out.get("errors"), out
