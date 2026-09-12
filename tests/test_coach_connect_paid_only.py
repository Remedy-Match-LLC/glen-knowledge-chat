"""Reaching an individual volunteer coach is for paid members only.

Glen, 2026-09-12: 1:1 coach connect is "only for paid members".

The three /api/community coach endpoints gated on an active coaching window alone.
A window is EARNED by a remedy delivery and only opened for somebody whose
membership was active at the order date, so the intent was already right at grant
time. But the window then runs 30 days from the delivery and nothing re-checked, so
a membership lapsing mid-window left 1:1 coach access open to a former member.

Not to be confused with the group-coaching gate. Group coaching is
_group_coaching_entitled — paid member OR certification student — and never
consults the window. The window governs the individual coach directory, requests
and waitlist only.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app_module():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")


@pytest.fixture
def cx():
    from dashboard import coaching as C
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    C.init_coaching_table(conn)
    yield conn
    conn.close()


def _window(cx, email):
    from dashboard import coaching as C
    C.open_window(cx, email=email, order_id=1, days=C.WINDOW_DAYS, source="delivery")


@pytest.mark.parametrize("has_window,is_paid,expected", [
    (True, True, True),      # the intended case
    (True, False, False),    # THE GAP: window still open, membership lapsed
    (False, True, False),    # paid but never earned a window
    (False, False, False),
])
def test_both_conditions_are_required(app_module, monkeypatch, cx,
                                      has_window, is_paid, expected):
    if has_window:
        _window(cx, "m@example.com")
    monkeypatch.setattr(app_module, "_is_paid_member", lambda e: is_paid)

    assert app_module._coach_connect_entitled(cx, "m@example.com") is expected


def test_a_lapsed_member_holding_a_live_window_is_refused(app_module, monkeypatch, cx):
    """Stated on its own because it is the only case that changed, and it is the
    one a client would notice."""
    _window(cx, "lapsed@example.com")
    from dashboard import coaching as C
    assert C.active_window(cx, "lapsed@example.com") is not None, "window is live"
    monkeypatch.setattr(app_module, "_is_paid_member", lambda e: False)

    assert app_module._coach_connect_entitled(cx, "lapsed@example.com") is False


def test_an_empty_email_is_refused_without_asking_about_membership(
        app_module, monkeypatch, cx):
    """Fail closed, and do not spend a membership lookup on a missing identity."""
    asked = []
    monkeypatch.setattr(app_module, "_is_paid_member",
                        lambda e: asked.append(e) or True)
    assert app_module._coach_connect_entitled(cx, "") is False
    assert app_module._coach_connect_entitled(cx, None) is False
    assert asked == []


def test_group_coaching_is_a_different_gate_and_is_unchanged(app_module, monkeypatch):
    """Group coaching must NOT start depending on a delivery-earned window, and it
    still admits certification students where 1:1 connect does not."""
    monkeypatch.setattr(app_module, "_is_paid_member", lambda e: False)
    monkeypatch.setattr(app_module, "_is_certification_student", lambda e: True)

    assert app_module._group_coaching_entitled("student@example.com") is True


def test_all_three_endpoints_use_the_shared_gate(app_module):
    """A fourth endpoint added later must not quietly reintroduce the window-only
    check, so pin that no caller is left on the old one."""
    import inspect
    src = inspect.getsource(app_module)
    for name in ("community_coaches", "community_coach_request",
                 "community_coach_waitlist"):
        fn = getattr(app_module, name, None)
        assert fn is not None, f"{name} is gone; this test needs updating"
        body = inspect.getsource(fn)
        assert "_coach_connect_entitled" in body, f"{name} is not using the gate"
        assert "_co.active_window" not in body, (
            f"{name} still gates on the window alone")
