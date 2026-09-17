"""A browser session holding an OWNER token must work on the console's data routes.

Glen, 2026-09-17: "Now, when she navigates to different tabs, it requires the console key
again."

Rae signed in successfully. Measured from production: her session was minted at 18:34:57 as
"Rae", scope workspace:rae, and last_used was 18:49:16, so the cookie was being sent and
accepted somewhere. But 200 requests answered 401 in that window, nine each across
/api/todos, /api/calendar, /api/money/wise, /api/brain, /api/ghl/pipeline and the rest:
the console dashboard's data calls. Each tab loaded, got 401, and fell back to asking for
the key.

THE CAUSE. dashboard.require_console_key guards 153 routes and had two separate credential
rules, neither of which covered her:

  _console_cookie_check  compares the cookie against the MASTER SECRET only
                         (hmac.compare_digest against one value)
  _owner_token_check     runs only `if key`, and `key` is the header or ?key=

Her cookie holds an owner TOKEN, deliberately: the verify route mints one so it can be
revoked on its own and never escalates to the master secret. So the first rule rejected it
and the second never ran, and she fell through to 401 with a perfectly valid session.

THE FIX is not a third rule. app.py's _present_console_key() already resolves every
credential shape, cookie included, and the decorator now asks it instead of keeping a
narrower duplicate in step with it.

WHAT MUST NOT CHANGE: a VA token (Shaira, scope workspace:shaira, role 'va') is still
rejected here. That is the whole point of _owner_token_check being the gate rather than
"is this a valid token".
"""
import importlib

import pytest


@pytest.fixture()
def dash():
    import dashboard
    importlib.reload(dashboard)
    return dashboard


def _wrap(dash, fn=None):
    return dash.require_console_key(fn or (lambda: ("ok", 200)))


# The 401 path calls jsonify(), which needs a Flask app context. The accept paths do not,
# which is why only the rejection tests need this.
def _refused(dash, guarded):
    import flask
    with flask.Flask(__name__).app_context():
        body, code = guarded()
    return code


def test_the_registration_hook_exists(dash):
    assert hasattr(dash, "set_presented_key_check")
    assert dash._presented_key_check is None, "it must start unregistered"


def test_a_cookie_borne_owner_token_is_accepted(dash, monkeypatch):
    """The reported bug. Her credential arrives in a cookie, not a header."""
    dash.CONSOLE_SECRET = "master"
    dash.set_owner_token_check(lambda k: k == "rae-owner-token")
    dash.set_console_cookie_check(lambda: False)      # not the master-secret cookie
    dash.set_presented_key_check(lambda: "rae-owner-token")

    calls = []
    guarded = _wrap(dash, lambda: (calls.append(1), ("ok", 200))[1])

    class Req:
        headers = {}
        args = {}
    monkeypatch.setattr(dash, "request", Req)
    assert guarded() == ("ok", 200), (
        "REGRESSION: an owner-token cookie was refused, which is the 401 on every tab"
    )
    assert calls, "the route body must actually run"


def test_a_va_token_in_a_cookie_is_still_rejected(dash, monkeypatch):
    """Scoped non-owner tokens stay out. This is why the gate is _owner_token_check."""
    dash.CONSOLE_SECRET = "master"
    dash.set_owner_token_check(lambda k: k == "rae-owner-token")
    dash.set_console_cookie_check(lambda: False)
    dash.set_presented_key_check(lambda: "shaira-va-token")

    guarded = _wrap(dash)

    class Req:
        headers = {}
        args = {}
    monkeypatch.setattr(dash, "request", Req)
    assert _refused(dash, guarded) == 401, "a VA token must not reach a console data route"


def test_the_master_secret_still_works_unchanged(dash, monkeypatch):
    dash.CONSOLE_SECRET = "master"
    dash.set_owner_token_check(lambda k: False)
    dash.set_console_cookie_check(lambda: False)
    dash.set_presented_key_check(lambda: "")

    guarded = _wrap(dash)

    class Req:
        headers = {"X-Console-Key": "master"}
        args = {}
    monkeypatch.setattr(dash, "request", Req)
    assert guarded() == ("ok", 200)


def test_a_master_secret_cookie_still_works_unchanged(dash, monkeypatch):
    """The path that already worked, for a browser holding the shared key."""
    dash.CONSOLE_SECRET = "master"
    dash.set_owner_token_check(lambda k: False)
    dash.set_console_cookie_check(lambda: True)
    dash.set_presented_key_check(lambda: "")

    guarded = _wrap(dash)

    class Req:
        headers = {}
        args = {}
    monkeypatch.setattr(dash, "request", Req)
    assert guarded() == ("ok", 200)


def test_a_resolver_that_raises_does_not_take_the_route_down(dash, monkeypatch):
    """It runs on 153 routes. A failure there must be a 401, never a 500."""
    dash.CONSOLE_SECRET = "master"
    dash.set_owner_token_check(lambda k: False)
    dash.set_console_cookie_check(lambda: False)

    def boom():
        raise RuntimeError("no request context")
    dash.set_presented_key_check(boom)

    guarded = _wrap(dash)

    class Req:
        headers = {}
        args = {}
    monkeypatch.setattr(dash, "request", Req)
    assert _refused(dash, guarded) == 401


def test_no_secret_configured_still_disables_auth(dash, monkeypatch):
    """Unchanged behaviour, and pinned because an absent gate opening is a real trap."""
    dash.CONSOLE_SECRET = ""
    guarded = _wrap(dash)
    assert guarded() == ("ok", 200)


def test_app_registers_the_resolver():
    """The wiring, not just the capability. Unregistered, the fix is inert."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "dashboard.set_presented_key_check(_present_console_key)" in src
