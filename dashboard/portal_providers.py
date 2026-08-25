"""OAuth/OpenID Connect helpers for client portal providers.

Flask routes own cookies and redirects. This module owns transaction state,
provider requests, and ID-token validation so both providers enter the same
portal identity/session seam.
"""

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

import jwt
import requests
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from dashboard.practitioner_portal import _ensure_auth_tokens
from dashboard.timeutil import is_expired as _is_expired


OAUTH_PURPOSE = "client_oauth_transaction"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"


def _hash(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def _claim_true(value):
    return value is True or (isinstance(value, str) and value.lower() == "true")


def create_transaction(cx, provider, redirect_uri):
    _ensure_auth_tokens(cx)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    now = datetime.now(timezone.utc)
    extra = {"provider": provider, "nonce": nonce, "verifier": verifier,
             "redirect_uri": redirect_uri}
    cx.execute(
        "INSERT INTO auth_tokens (token_hash,email,purpose,extra,created_at,expires_at) VALUES (?,?,?,?,?,?)",
        (_hash(state), "", OAUTH_PURPOSE, json.dumps(extra), now.isoformat(),
         (now + timedelta(minutes=10)).isoformat()),
    )
    cx.commit()
    return state, nonce, verifier


def consume_transaction(cx, state, provider):
    if not state:
        return None
    _ensure_auth_tokens(cx)
    row = cx.execute(
        "SELECT extra,expires_at,consumed_at FROM auth_tokens WHERE token_hash=? AND purpose=?",
        (_hash(state), OAUTH_PURPOSE),
    ).fetchone()
    if not row or row[2] or _is_expired(row[1]):
        return None
    try:
        extra = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        return None
    if extra.get("provider") != provider:
        return None
    cur = cx.execute(
        "UPDATE auth_tokens SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), _hash(state)),
    )
    cx.commit()
    return extra if cur.rowcount == 1 else None


def google_authorization_url(client_id, redirect_uri, state, nonce, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })


def exchange_google(code, client_id, client_secret, transaction):
    response = requests.post(GOOGLE_TOKEN_URL, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": transaction["redirect_uri"], "grant_type": "authorization_code",
        "code_verifier": transaction["verifier"],
    }, timeout=15)
    response.raise_for_status()
    claims = google_id_token.verify_oauth2_token(
        response.json()["id_token"], google_requests.Request(), client_id)
    if claims.get("nonce") != transaction["nonce"] or not _claim_true(claims.get("email_verified")):
        raise ValueError("invalid Google identity claims")
    return {"provider": "google", "subject": claims["sub"],
            "email": (claims.get("email") or "").lower(), "name": claims.get("name") or ""}


def _apple_client_secret(team_id, client_id, key_id, private_key):
    now = datetime.now(timezone.utc)
    return jwt.encode({"iss": team_id, "iat": int(now.timestamp()),
                       "exp": int((now + timedelta(minutes=5)).timestamp()),
                       "aud": "https://appleid.apple.com", "sub": client_id},
                      private_key, algorithm="ES256", headers={"kid": key_id})


def apple_authorization_url(client_id, redirect_uri, state, nonce):
    return APPLE_AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code id_token",
        "response_mode": "form_post", "scope": "name email", "state": state, "nonce": nonce,
    })


def exchange_apple(code, id_token, client_id, team_id, key_id, private_key, transaction, user=None):
    response = requests.post(APPLE_TOKEN_URL, data={
        "code": code, "client_id": client_id,
        "client_secret": _apple_client_secret(team_id, client_id, key_id, private_key),
        "redirect_uri": transaction["redirect_uri"], "grant_type": "authorization_code",
    }, timeout=15)
    response.raise_for_status()
    returned_token = response.json().get("id_token") or id_token
    signing_key = jwt.PyJWKClient(APPLE_KEYS_URL).get_signing_key_from_jwt(returned_token)
    claims = jwt.decode(returned_token, signing_key.key, algorithms=["RS256"],
                        audience=client_id, issuer="https://appleid.apple.com")
    if claims.get("nonce") != transaction["nonce"] or not _claim_true(claims.get("email_verified")):
        raise ValueError("invalid Apple identity claims")
    name = ""
    if user:
        try:
            obj = json.loads(user) if isinstance(user, str) else user
            parts = obj.get("name") or {}
            name = " ".join(x for x in (parts.get("firstName"), parts.get("lastName")) if x)
        except (TypeError, json.JSONDecodeError):
            pass
    return {"provider": "apple", "subject": claims["sub"],
            "email": (claims.get("email") or "").lower(), "name": name}
