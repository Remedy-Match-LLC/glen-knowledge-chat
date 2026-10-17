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


def console_back_path(referrer, host):
    """The console page the launcher was clicked from, as a path, or "".

    Only a same-host /console page counts, so the local tool's back button can never
    be pointed anywhere else."""
    try:
        u = urllib.parse.urlsplit(referrer or "")
    except ValueError:
        return ""
    if not host or u.netloc.lower() != host.lower() or u.scheme not in ("http", "https"):
        return ""
    path = u.path or ""
    if not (path == "/console" or path.startswith("/console/")) or "//" in path:
        return ""
    if path.startswith("/console/biofield-intake"):
        return ""
    return path + ("?" + u.query if u.query else "")


def local_tool_url(base_env, default_base, secret, path="/", back=""):
    """Where to send the browser for a local tool. `secret` is the server's own
    current console key; a blank one yields a keyless URL rather than `?key=`.
    `back` is the console path the tool's back button returns to."""
    base = (os.environ.get(base_env) or default_base).rstrip("/")
    path = "/" + (path or "").strip("/")
    secret = (secret or "").strip()
    params = []
    if secret:
        params.append("key=" + urllib.parse.quote(secret, safe=""))
    if back:
        params.append("back=" + urllib.parse.quote(back, safe=""))
    return base + path + ("?" + "&".join(params) if params else "")
