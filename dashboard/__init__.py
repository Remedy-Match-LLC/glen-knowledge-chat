"""Command Center Dashboard — modular integrations for the /dashboard route."""

import os
from functools import wraps
from flask import request, jsonify

CONSOLE_SECRET = os.environ.get("CONSOLE_SECRET", "")

# Optional hook: a callable(key) -> bool that grants console access to an
# OWNER-role per-user token (e.g. Rae). app.py registers it via
# set_owner_token_check() so the legacy decorator can accept owner tokens in
# addition to CONSOLE_SECRET, without coupling this module to the DB/rbac.
_owner_token_check = None

# Optional hook: a callable() -> bool that reports whether the current request
# carries a valid signed console-login COOKIE (see app.py's _console_browser_login).
# Registered by app.py via set_console_cookie_check() so a browser that logged in
# — and therefore sends no key in the URL, only the cookie — is still authorized
# here, without coupling this module to app.py's cookie machinery.
_console_cookie_check = None

# Registered by app.py via set_presented_key_check(). Resolves whatever credential this
# request actually carries, INCLUDING a login cookie holding an owner token. The cookie
# check above only knows the master-secret cookie, which is a narrower rule than the one
# app.py already implements in _present_console_key().
_presented_key_check = None


def set_owner_token_check(fn):
    """Register the owner-token validator (see _owner_token_check)."""
    global _owner_token_check
    _owner_token_check = fn


def set_presented_key_check(fn):
    """Register the resolver for whatever credential a request carries (see
    _presented_key_check)."""
    global _presented_key_check
    _presented_key_check = fn


def set_console_cookie_check(fn):
    """Register the console-login-cookie validator (see _console_cookie_check)."""
    global _console_cookie_check
    _console_cookie_check = fn


def require_console_key(fn):
    """Decorator: require X-Console-Key (or ?key=). Accepts CONSOLE_SECRET, a valid
    signed console-login cookie (browser sessions), or an OWNER-role per-user token
    when the respective check is registered. Scoped non-owner tokens (e.g. a VA) are
    rejected here."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not CONSOLE_SECRET:
            return fn(*args, **kwargs)  # auth disabled if no secret set
        key = request.headers.get("X-Console-Key", "") or request.args.get("key", "")
        if key == CONSOLE_SECRET:
            return fn(*args, **kwargs)
        if not key and _console_cookie_check is not None:
            try:
                if _console_cookie_check():
                    return fn(*args, **kwargs)
            except Exception:
                pass
        # An OWNER token, however it was presented. `key` covers the header and ?key=;
        # _presented_key_check resolves a LOGIN COOKIE holding an owner token, which is
        # what a browser session actually carries.
        #
        # Rae could sign in and then got 401 on every console tab. Her cookie holds an
        # owner token, by design: the verify route mints one so it can be revoked on its
        # own and never escalates to the master secret. But _console_cookie_check above
        # only recognises a cookie holding CONSOLE_SECRET itself, and this branch used to
        # require `key`, which is empty for a cookie session. So she fell through to 401
        # on all 153 routes behind this decorator while her session was perfectly valid,
        # and the console asked for the key again on every tab.
        # Glen, 2026-09-17: "when she navigates to different tabs, it requires the
        # console key again."
        presented = key
        if not presented and _presented_key_check is not None:
            try:
                presented = _presented_key_check() or ""
            except Exception:
                presented = ""
        if presented and _owner_token_check is not None:
            try:
                if _owner_token_check(presented):
                    return fn(*args, **kwargs)
            except Exception:
                pass
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return wrapper


def ok(data, **extra):
    """Standard success envelope."""
    return jsonify({"ok": True, "data": data, **extra})


def fail(error, status=500, **extra):
    """Standard failure envelope."""
    return jsonify({"ok": False, "error": str(error), **extra}), status
