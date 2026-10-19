"""Fullscript API: Glen's practitioner OAuth and catalog search (Glen, 2026-10-18).

The first feature is wider catalog search, beyond the 24 seeded products. Glen
authorises once in the browser as practitioner; the app keeps the token.

The token is stored encrypted in the database (Glen, 2026-10-18: "yes"). Fullscript
rotates the refresh token on every refresh, so it cannot be a fixed Doppler value.
The key is FULLSCRIPT_TOKEN_KEY in Doppler; without it nothing is stored or read.

Fullscript's refresh rule: the old refresh token stays usable until the new access
token is first used. So a refresh is saved and committed before its access token
is used, and a failed save can retry with the old one.

Search results carry no ingredients, so the herbal check cannot run on them yet.
Each result says herbal is unchecked rather than passing as not herbal.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

SANDBOX_BASE = "https://api-us-snd.fullscript.io/api"
REDIRECT_URI = "https://illtowell.com/fullscript/oauth/callback"
SCOPE = "catalog:read"
PROVIDER = "fullscript"
# Refresh this many seconds before the access token's stated expiry.
EARLY = 300
TIMEOUT = 20


class NotConnected(Exception):
    """No usable token: Glen has not authorised, or the key is missing."""


def environment():
    return "sandbox"


def base_url():
    return SANDBOX_BASE


def credentials():
    """(client id, client secret), or ("", "") when either is absent."""
    cid = os.environ.get("FULLSCRIPT_SANDBOX_CLIENT_ID", "").strip()
    secret = os.environ.get("FULLSCRIPT_SANDBOX_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else ("", "")


def _fernet():
    key = os.environ.get("FULLSCRIPT_TOKEN_KEY", "").strip()
    if not key:
        raise NotConnected("FULLSCRIPT_TOKEN_KEY is not set")
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def ready():
    """What is missing before Glen can authorise, as plain lines. Empty means ready."""
    missing = []
    if credentials() == ("", ""):
        missing.append("The Fullscript sandbox client ID or secret is not in Doppler.")
    try:
        _fernet()
    except NotConnected:
        missing.append("FULLSCRIPT_TOKEN_KEY is not in Doppler.")
    except Exception:
        missing.append("FULLSCRIPT_TOKEN_KEY is not a valid key.")
    return missing


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS fullscript_oauth_token (
        environment TEXT PRIMARY KEY, sealed TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def authorization_url(state):
    cid, _ = credentials()
    return base_url() + "/oauth/authorize?" + urlencode({
        "client_id": cid, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "scope": SCOPE, "state": state})


def _token_request(body, post=None):
    cid, secret = credentials()
    if not cid:
        raise NotConnected("no Fullscript client credentials")
    post = post or requests.post
    resp = post(base_url() + "/oauth/token", json=dict(
        body, client_id=cid, client_secret=secret, redirect_uri=REDIRECT_URI),
        timeout=TIMEOUT)
    if resp.status_code != 200:
        # The body can echo a token, so only the status is kept.
        raise RuntimeError(f"Fullscript token request failed: HTTP {resp.status_code}")
    data = resp.json()
    token = data.get("oauth") if isinstance(data.get("oauth"), dict) else data
    if not token.get("access_token") or not token.get("refresh_token"):
        raise RuntimeError("Fullscript token response had no access or refresh token")
    expires_in = int(token.get("expires_in") or 7200)
    created = int(token.get("created_at") or time.time())
    return {"access_token": token["access_token"], "refresh_token": token["refresh_token"],
            "expires_at": created + expires_in}


def _seal(token):
    return _fernet().encrypt(json.dumps(token).encode()).decode()


def _unseal(sealed):
    return json.loads(_fernet().decrypt(sealed.encode()).decode())


def _stored(cx):
    ensure_schema(cx)
    row = cx.execute("SELECT sealed FROM fullscript_oauth_token WHERE environment=?",
                     (environment(),)).fetchone()
    return row[0] if row else None


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_new(cx, token):
    """Store a token from Glen's authorisation, replacing any earlier one."""
    from dashboard import dbwrite
    sealed = _seal(token)
    ensure_schema(cx)
    dbwrite.insert_or_replace(cx, "fullscript_oauth_token",
                              ("environment", "sealed", "updated_at"),
                              (environment(), sealed, _now()), conflict_cols=("environment",))
    cx.commit()


def exchange_code(cx, code, post=None):
    """Swap the code from the callback for a token, and store it."""
    token = _token_request({"grant_type": "authorization_code", "code": code}, post)
    save_new(cx, token)
    return token


def connected(cx):
    """True when a token is stored and the key can read it."""
    sealed = _stored(cx)
    if not sealed:
        return False
    try:
        _unseal(sealed)
        return True
    except Exception:
        return False


def access_token(cx, post=None, now=None):
    """A current access token, refreshing first when it is near expiry.

    The refreshed token is committed before it is returned. If another worker
    refreshed in between, its stored token is used instead of this one."""
    sealed = _stored(cx)
    if not sealed:
        raise NotConnected("Fullscript is not connected")
    try:
        token = _unseal(sealed)
    except NotConnected:
        raise
    except Exception:
        raise NotConnected("the stored Fullscript token cannot be read with this key")
    if (now or time.time()) < token["expires_at"] - EARLY:
        return token["access_token"]
    fresh = _token_request({"grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"]}, post)
    cur = cx.execute("UPDATE fullscript_oauth_token SET sealed=?, updated_at=? "
                     "WHERE environment=? AND sealed=?",
                     (_seal(fresh), _now(), environment(), sealed))
    cx.commit()
    if cur.rowcount != 1:
        # Another worker saved first. Its token is the one Fullscript keeps.
        sealed = _stored(cx)
        if not sealed:
            raise NotConnected("Fullscript was disconnected during a refresh")
        return _unseal(sealed)["access_token"]
    return fresh["access_token"]


def disconnect(cx):
    ensure_schema(cx)
    cx.execute("DELETE FROM fullscript_oauth_token WHERE environment=?", (environment(),))
    cx.commit()


def _tokens(term):
    """Meaningful (4+ character) tokens of a search term, as the seed builder uses."""
    return [t for t in re.split(r"[^a-z0-9]+", str(term or "").lower()) if len(t) >= 4]


def relevant(name, term):
    """Keep a hit only if a meaningful token of the term is in its name. Fullscript's
    search is fuzzy; this is the seed builder's filter."""
    words = set(re.split(r"[^a-z0-9]+", str(name or "").lower()))
    return any(t in words for t in _tokens(term))


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


def blocked(name):
    """True for a product on the never-recommend list, matched as whole words anywhere
    in its name, so a brand or qualifier in front does not hide it."""
    from dashboard.related_products import DO_NOT_RECOMMEND
    slug = f"-{_slug(name)}-"
    return any(f"-{s}-" in slug for s in DO_NOT_RECOMMEND)


def search(cx, term, get=None, post=None):
    """{"term", "results", "dropped"} for a catalog search. Results pass the relevance
    filter and the never-recommend list; each is marked herbal unchecked. Dropped
    hits are listed with the reason, so nothing disappears without a word."""
    if not _tokens(term):
        raise ValueError("the search needs a word of 4 or more letters")
    get = get or requests.get
    resp = get(base_url() + "/catalog/search/products", params={"query": term},
               headers={"Authorization": f"Bearer {access_token(cx, post)}"},
               timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Fullscript search failed: HTTP {resp.status_code}")
    results, dropped = [], []
    for product in resp.json().get("products") or []:
        name = product.get("name") or ""
        brand = (product.get("brand") or {}).get("name") or ""
        if not relevant(name, term):
            dropped.append({"name": name, "brand": brand, "why": "no search word in its name"})
            continue
        if blocked(name):
            dropped.append({"name": name, "brand": brand, "why": "on the never-recommend list"})
            continue
        variant = product.get("primary_variant") or {}
        results.append({"id": product.get("id"), "name": name, "brand": brand,
                        "status": product.get("status") or variant.get("status") or "",
                        "availability": variant.get("availability") or "",
                        "msrp": variant.get("msrp") or "", "herbal": "unchecked"})
    return {"term": term, "results": results, "dropped": dropped}
