"""The auth routes must record the caller's real address, not the one they sent.

`tests/test_client_address.py` proves the helper. This proves the ROUTES use it, which is a
different question and the one that has bitten this work before: a helper that is never
called and a helper that is correct look identical from outside.

The bug being fixed: all three read `.split(",")[0]`, which is client-written. Measured on
production 2026-09-11, a request claiming `X-Forwarded-For: 203.0.113.77` was stored by
`portal_auth._record_event` with `ip_hash` = sha256 of that value. So an attacker could
attribute their own failed logins to any address, including a real customer's.
"""
import re
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parents[1] / "app.py").read_text()

ROUTES = ("client_password_login", "client_password_reset", "healing_oasis_request")


def _body(fn):
    i = APP.index(f"def {fn}(")
    nxt = APP.find("\n@app.route", i)
    return APP[i:nxt if nxt != -1 else len(APP)]


def test_no_auth_route_still_takes_the_client_written_element():
    """The literal bug. If this string comes back anywhere in app.py, something regressed
    or a new route copied the old pattern."""
    bad = '(request.headers.get("X-Forwarded-For", "") or request.remote_addr or "").split(",")[0]'
    assert bad not in APP, "a route is reading the caller-written end of the chain again"


@pytest.mark.parametrize("fn", ROUTES)
def test_each_route_derives_the_address_through_the_helper(fn):
    body = _body(fn)
    assert "_client_address.client_address(" in body, f"{fn} does not use the helper"


@pytest.mark.parametrize("fn", ROUTES)
def test_each_route_reports_when_the_assumption_did_not_hold(fn):
    """A wrong hop count is silent otherwise, which is exactly how the last attempt shipped
    broken. The route must say so when the chain was not the expected shape, or when
    Cloudflare's own header disagrees with the hop count."""
    body = _body(fn)
    assert "[client-ip] source=" in body, f"{fn} fails quietly if the chain changes"
    assert 'if _ip_src != "xff" or _cf_ok is False:' in body


def test_the_helper_is_imported_once_at_module_level():
    """Not inside a function, so a missing module fails at import rather than at the first
    failed login."""
    assert re.search(r"^from dashboard import client_address as _client_address$",
                     APP, re.M)
