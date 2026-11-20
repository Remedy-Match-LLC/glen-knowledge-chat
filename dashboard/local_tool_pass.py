"""One-time passes that let an OWNER token open the local tools over the private link.

Biofield Intake and Clinical Tags run on Glen's Mac, so client health data never
reaches the hosted server. The hosted console launches them. The master console key
used to be the only way in, and an owner token must never escalate to it.

So the hosted console mints a pass instead. A pass is signed with
LOCAL_TOOL_PASS_SECRET, names the owner and the tool, lasts PASS_TTL_S seconds, and
works once. The local tool redeems it for its own session cookie. Both sides read the
same secret from Doppler, and neither side ever puts the master key in a URL.

Every check fails closed. No secret, a short secret, a bad signature, an expired pass,
a replayed pass or a pass for the other tool are all refused.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time

SECRET_ENV = "LOCAL_TOOL_PASS_SECRET"
MIN_SECRET_LEN = 32
PASS_TTL_S = 120
SESSION_TTL_S = 12 * 60 * 60
SESSION_COOKIE = "rm_local_session"

# Tool name -> the path it opens at on the local server.
TOOLS = {"biofield-intake": "/", "clinical-tags": "/clinical-tags"}

# Tailscale hands out addresses from the carrier-grade NAT block, never a LAN one.
TAILNET_NET = ipaddress.ip_network("100.64.0.0/10")

_PASS_LABEL = b"local-tool-pass-v1."
_SESSION_LABEL = b"local-tool-session-v1."


class PassError(Exception):
    """A pass or session was refused. str(e) is a short reason, safe to log."""


def pass_secret():
    """The signing secret, or "" when it is unset or too short to trust."""
    s = (os.environ.get(SECRET_ENV) or "").strip()
    return s if len(s) >= MIN_SECRET_LEN else ""


def tool_for_path(path):
    """The tool a local path opens, or None when it opens neither."""
    p = "/" + (path or "").strip("/")
    for tool, tpath in TOOLS.items():
        if p == tpath:
            return tool
    return None


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s):
    s = s or ""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(label, body, secret):
    return _b64(hmac.new(secret.encode("utf-8"), label + body.encode("ascii"),
                         hashlib.sha256).digest())


def _open(label, token, secret):
    """Check the signature and return the payload dict. Raises PassError."""
    if not secret:
        raise PassError("no secret")
    body, dot, sig = (token or "").partition(".")
    if not dot or not body or not sig:
        raise PassError("malformed")
    if not hmac.compare_digest(sig, _sign(label, body, secret)):
        raise PassError("bad signature")
    try:
        data = json.loads(_unb64(body))
    except Exception:
        raise PassError("malformed")
    if not isinstance(data, dict):
        raise PassError("malformed")
    return data


def mint_pass(owner, tool, now=None, secret=None):
    """A signed one-time pass for `owner` to open `tool`. Raises PassError when there
    is no secret, so a caller can never hand out an unsigned pass."""
    secret = pass_secret() if secret is None else secret
    if not secret:
        raise PassError("no secret")
    if tool not in TOOLS:
        raise PassError("unknown tool")
    owner = (owner or "").strip()
    if not owner:
        raise PassError("no owner")
    now = int(time.time() if now is None else now)
    payload = {"v": 1, "owner": owner, "tool": tool, "iat": now,
               "exp": now + PASS_TTL_S, "jti": secrets.token_urlsafe(16)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + "." + _sign(_PASS_LABEL, body, secret)


class ReplayGuard:
    """Remembers redeemed pass ids until they expire, so each pass works once.

    It lives in memory, so a restart forgets it. To keep a pass from working twice
    across a restart, any pass issued before this guard was made is refused
    (round-2 review, 2026-11-19). The cost is one fresh click after a restart."""

    def __init__(self, not_before=None):
        self._seen = {}
        self._lock = threading.Lock()
        self._not_before = int(time.time()) if not_before is None else int(not_before)

    def consume(self, jti, exp, now, iat=None):
        if iat is None or iat < self._not_before:
            return False
        with self._lock:
            for k in [k for k, e in self._seen.items() if e < now]:
                del self._seen[k]
            if jti in self._seen:
                return False
            self._seen[jti] = exp
            return True


def redeem_pass(token, tool, guard, now=None, secret=None):
    """Verify a pass for `tool` and burn it. Returns the owner. Raises PassError."""
    secret = pass_secret() if secret is None else secret
    data = _open(_PASS_LABEL, token, secret)
    now = int(time.time() if now is None else now)
    if data.get("v") != 1:
        raise PassError("wrong version")
    if data.get("tool") != tool or tool not in TOOLS:
        raise PassError("wrong tool")
    exp, iat = data.get("exp"), data.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int):
        raise PassError("malformed")
    if exp < now or exp - iat > PASS_TTL_S or iat > now + 30:
        raise PassError("expired")
    owner, jti = data.get("owner"), data.get("jti")
    if not owner or not isinstance(owner, str) or not jti or not isinstance(jti, str):
        raise PassError("malformed")
    if not guard.consume(jti, exp, now, iat=iat):
        raise PassError("already used")
    return owner


def mint_session(owner, now=None, secret=None):
    """The local tool's own session cookie value, signed apart from passes."""
    secret = pass_secret() if secret is None else secret
    if not secret:
        raise PassError("no secret")
    now = int(time.time() if now is None else now)
    body = _b64(json.dumps({"v": 1, "owner": owner, "exp": now + SESSION_TTL_S},
                           separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + "." + _sign(_SESSION_LABEL, body, secret)


def session_owner(value, now=None, secret=None):
    """The owner a session cookie names, or None when it is missing, forged or old."""
    secret = pass_secret() if secret is None else secret
    try:
        data = _open(_SESSION_LABEL, value, secret)
    except PassError:
        return None
    now = int(time.time() if now is None else now)
    exp = data.get("exp")
    if data.get("v") != 1 or not isinstance(exp, int) or exp < now:
        return None
    owner = data.get("owner")
    return owner if isinstance(owner, str) and owner else None


def check_tailnet_host(host):
    """Return `host` if it is a single Tailscale address, else raise ValueError.

    This refuses 0.0.0.0, loopback, a LAN address and any hostname, so the local tool
    can never be told to listen on the home network."""
    ip = ipaddress.ip_address((host or "").strip())
    if ip.version != 4 or ip not in TAILNET_NET:
        raise ValueError(f"{host!r} is not a Tailscale (100.64.0.0/10) address")
    return str(ip)
