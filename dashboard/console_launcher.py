"""Redirect targets for the console tabs that launch a LOCAL tool.

Biofield Intake and Clinical Tags run on Glen's own Mac, so the console tab is a
launcher: it authenticates the caller, then bounces the browser to 127.0.0.1 with
the console key so the local app can gate on it.

Glen, 2026-09-16: clicking the Intake tab gave "Unauthorized — open this from the
console 'Biofield Intake' link", which is the one link that was supposed to work.

The launcher used to echo back whatever `?key=` the browser sent. op-nav.js builds
that from localStorage, so after a key rotation every browser forwarded a dead key,
and a browser with empty localStorage forwarded none at all. Either way the local
app refused it.

By the time the launcher runs, the caller is already authenticated — by key, by the
console cookie, or by an owner token. So it hands over the key THIS SERVER holds.
A rotation then fixes every browser at once instead of breaking every browser at
once.
"""
import os
import urllib.parse


def local_tool_url(base_env, default_base, secret, path="/"):
    """Where to send the browser for a local tool. `secret` is the server's own
    current console key; a blank one yields a keyless URL rather than `?key=`."""
    base = (os.environ.get(base_env) or default_base).rstrip("/")
    path = "/" + (path or "").strip("/")
    secret = (secret or "").strip()
    if not secret:
        return base + path
    return base + path + "?key=" + urllib.parse.quote(secret, safe="")
