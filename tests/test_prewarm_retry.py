# tests/test_prewarm_retry.py
"""The QB cache prewarm retries instead of giving up after one attempt.

It warms a 5-minute cache so the first dashboard request does not pay a
cold-cache failure. In production it failed 89 of 96 times (93%) — so it never
did that job — and those failures were ~45% of all Postgres SSL errors in the
logs, burying the user-facing ones.

Every failure landed within seconds of a worker boot ("server closed the
connection unexpectedly", "SSL error: ssl/tls alert bad record mac"), which is
the signature of racing something still starting. The underlying race was NOT
identified: there is no --preload (so the two gunicorn workers import
independently and share no sockets), and the token read/write both scope their
connection correctly. Backing off is the fix that works either way.
"""
import ast
import pathlib

import pytest


def _prewarm_src():
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_prewarm_caches":
            return ast.unparse(node)
    return ""


def _delays():
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "PREWARM_ATTEMPT_DELAYS"):
            return ast.literal_eval(node.value)
    return None


def test_there_is_more_than_one_attempt():
    d = _delays()
    assert d is not None, "PREWARM_ATTEMPT_DELAYS is gone"
    assert len(d) >= 2, f"only {len(d)} attempt(s) — a single try is what failed 93% of the time"


def test_the_delays_back_off():
    """A boot race needs the later attempts further out, not three quick pokes."""
    d = _delays()
    assert list(d) == sorted(d), f"delays not increasing: {d}"
    assert d[-1] >= 30, f"last attempt at {d[-1]}s is still inside the boot window"


def test_it_still_tries_early():
    """The point is a WARM cache. Waiting 45s for the first attempt would leave
    the dashboard cold through the window the prewarm exists to cover."""
    d = _delays()
    assert d[0] <= 5, f"first attempt at {d[0]}s is too late to warm anything"


def test_success_stops_the_loop():
    """Three attempts must not mean three QuickBooks token refreshes on every
    boot when the first one works."""
    src = _prewarm_src()
    assert "return" in src, "no early return — it would keep calling after success"


def test_only_the_final_giveup_logs_a_failure():
    """Otherwise the log line stops meaning 'the cache is cold' and starts
    meaning 'an attempt bounced', and the 45%-of-errors problem comes back
    three times worse."""
    src = _prewarm_src()
    assert "failed after" in src, "no distinct give-up message"
    assert "attempt" in src, "per-attempt lines are not distinguishable from the give-up"


def test_it_never_raises_out_of_the_thread():
    """An unhandled exception here printed a raw gevent Greenlet traceback into
    the logs, which is where the noise came from."""
    src = _prewarm_src()
    assert "except Exception" in src
