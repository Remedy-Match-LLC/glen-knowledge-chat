"""A response must take the same time whatever the answer.

`/portal/login-request` answers identically for a real address and an unknown one, which is
deliberate. It did not take the same TIME. Measured on production 2026-09-11:

    found    ~0.9s   (764ms and 749ms of it inside the mail send, on two real sends)
    missing  0.14 - 0.19s   (five direct measurements)

So the message revealed nothing and the clock revealed everything.

These tests do not sleep. The policy is a pure function and the route is checked
structurally, because a test that really waits 1.5s is a test somebody eventually deletes.

Run:  python3 -m pytest tests/test_response_timing.py -q
"""
import re
from pathlib import Path

import pytest

from dashboard.response_timing import TARGET_SECONDS, overran, remaining

APP = (Path(__file__).resolve().parents[1] / "app.py").read_text()
ROUTES = ("client_login_request", "client_password_reset_request")


def _body(fn):
    i = APP.index(f"def {fn}(")
    nxt = APP.find("\n@app.route", i)
    return APP[i:nxt if nxt != -1 else len(APP)]


# ── the policy ────────────────────────────────────────────────────────────────
def test_a_fast_answer_waits_for_the_difference():
    assert remaining(0.16) == pytest.approx(TARGET_SECONDS - 0.16)


def test_a_slow_answer_waits_for_nothing():
    assert remaining(TARGET_SECONDS + 0.5) == 0.0


def test_both_measured_branches_end_at_the_same_total():
    """The whole point, stated as the two numbers actually measured in production."""
    found, missing = 0.9, 0.16
    assert found + remaining(found) == pytest.approx(missing + remaining(missing))


def test_the_target_clears_the_slowest_measured_found_branch():
    """If the target ever drops under the real found branch, the tail leaks again and
    nothing else in this file would notice."""
    assert TARGET_SECONDS > 0.9, "must exceed the measured found branch"


def test_a_clock_that_goes_backwards_does_not_produce_a_huge_wait():
    assert remaining(-5) == 0.0
    assert remaining(float("nan")) == 0.0


def test_garbage_does_not_raise():
    """This runs on every request to these routes. It must never be the thing that 500s."""
    assert remaining(None) == 0.0
    assert remaining("x") == 0.0
    assert overran(None) is False


def test_an_overrun_is_detectable():
    assert overran(TARGET_SECONDS + 0.1) is True
    assert overran(TARGET_SECONDS - 0.1) is False


# ── the routes must actually use it ───────────────────────────────────────────
@pytest.mark.parametrize("fn", ROUTES)
def test_the_route_starts_a_clock_before_it_looks_anything_up(fn):
    """If the clock starts after the lookup, the pad measures the wrong interval."""
    body = _body(fn)
    t0 = body.index("_t0 = time.monotonic()")
    lookup = body.index("FROM people WHERE")
    assert t0 < lookup, f"{fn} starts its clock after the lookup"


@pytest.mark.parametrize("fn", ROUTES)
def test_the_route_pads_before_returning(fn):
    body = _body(fn)
    assert "_equalise_response_time(_t0" in body, f"{fn} never pads"


@pytest.mark.parametrize("fn", ROUTES)
def test_the_pad_is_on_the_path_both_branches_take(fn):
    """Padding only the found branch would invert the leak rather than close it. The call
    must sit after the if/else has rejoined, immediately before the single return."""
    body = _body(fn)
    pad = body.index("_equalise_response_time(_t0")
    ret = body.index("return jsonify", pad)
    between = body[pad:ret]
    assert "if " not in between, f"{fn} pads inside a branch, not on the shared path"


def test_an_overrun_is_reported_rather_than_swallowed():
    """A target that has drifted below reality is the failure mode here, and it is
    completely invisible without this."""
    i = APP.index("def _equalise_response_time(")
    body = APP[i:i + 1400]
    assert "[resp-timing] overran" in body
    assert "_response_timing.overran(" in body


def test_the_password_login_route_is_deliberately_not_padded():
    """It already equalises with the password hash on the unknown branch, and it can only
    reveal who has a password. Padding it too would be cargo-culting the fix."""
    assert "_equalise_response_time" not in _body("client_password_login")
