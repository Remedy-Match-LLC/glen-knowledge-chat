"""Password authentication for the client portal.

This module is Flask-free and shares the existing client-session tokens from
``portal_identity``.  Password login is additive: magic links and durable
portal links remain valid regardless of this module or its feature flag.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from dashboard.practitioner_portal import _ensure_auth_tokens
from dashboard.timeutil import is_expired as _is_expired


RESET_PURPOSE = "client_password_reset"
RESET_TTL_MIN = 30
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_FAILED_ATTEMPTS = 10
LOCK_MINUTES = 15

_hasher = PasswordHasher()


def _now():
    return datetime.now(timezone.utc)


def _hash_token(token):
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def init_portal_auth_tables(cx):
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_credentials (
            person_id           INTEGER PRIMARY KEY,
            password_hash       TEXT NOT NULL,
            password_set_at     TEXT NOT NULL,
            password_changed_at TEXT NOT NULL,
            failed_attempts     INTEGER NOT NULL DEFAULT 0,
            locked_until        TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
        """
    )
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_auth_events (
            event_id        TEXT PRIMARY KEY,
            person_id       INTEGER,
            email_hash      TEXT,
            event           TEXT NOT NULL,
            provider        TEXT NOT NULL DEFAULT 'password',
            ip_hash         TEXT,
            user_agent      TEXT,
            created_at      TEXT NOT NULL,
            metadata        TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_portal_auth_events_person "
        "ON portal_auth_events(person_id, created_at)"
    )
    cx.commit()


def validate_password(password):
    if not isinstance(password, str):
        return False, "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, "Password is too long."
    return True, ""


def _record_event(cx, event, *, person_id=None, email="", ip="", user_agent="", metadata=None):
    init_portal_auth_tables(cx)
    email_hash = hashlib.sha256((email or "").strip().lower().encode()).hexdigest() if email else None
    ip_hash = hashlib.sha256((ip or "").encode()).hexdigest() if ip else None
    cx.execute(
        "INSERT INTO portal_auth_events "
        "(event_id, person_id, email_hash, event, provider, ip_hash, user_agent, created_at, metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (secrets.token_hex(16), person_id, email_hash, event, "password", ip_hash,
         (user_agent or "")[:500], _now().isoformat(), json.dumps(metadata or {})),
    )
    cx.commit()


def set_password(cx, person_id, password, *, email="", ip="", user_agent=""):
    ok, message = validate_password(password)
    if not ok:
        raise ValueError(message)
    init_portal_auth_tables(cx)
    now = _now().isoformat()
    encoded = _hasher.hash(password)
    cx.execute(
        "INSERT INTO portal_credentials "
        "(person_id,password_hash,password_set_at,password_changed_at,failed_attempts,locked_until,created_at,updated_at) "
        "VALUES (?,?,?,?,0,NULL,?,?) "
        "ON CONFLICT(person_id) DO UPDATE SET password_hash=excluded.password_hash, "
        "password_changed_at=excluded.password_changed_at, failed_attempts=0, "
        "locked_until=NULL, updated_at=excluded.updated_at",
        (person_id, encoded, now, now, now, now),
    )
    cx.commit()
    _record_event(cx, "password_set", person_id=person_id, email=email, ip=ip, user_agent=user_agent)


def verify_password(cx, email, password, *, ip="", user_agent=""):
    """Return person_id on success, otherwise None. Public callers must use one
    generic failure response so this result cannot enumerate portal accounts."""
    init_portal_auth_tables(cx)
    normalized = (email or "").strip().lower()
    row = cx.execute(
        "SELECT p.id, c.password_hash, c.locked_until, c.failed_attempts FROM people p "
        "JOIN portal_credentials c ON c.person_id=p.id WHERE lower(p.email)=?",
        (normalized,),
    ).fetchone()
    if not row:
        # Equalize the expensive part of known and unknown account checks.
        _hasher.hash(password or "")
        _record_event(cx, "login_failed", email=normalized, ip=ip, user_agent=user_agent)
        return None
    person_id, encoded, locked_until, failed_attempts = row
    if locked_until and not _is_expired(locked_until):
        _record_event(cx, "login_failed", person_id=person_id, email=normalized,
                      ip=ip, user_agent=user_agent, metadata={"reason": "locked"})
        return None
    try:
        valid = _hasher.verify(encoded, password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        valid = False
    if not valid:
        attempts = int(failed_attempts or 0) + 1
        lock_until = ((_now() + timedelta(minutes=LOCK_MINUTES)).isoformat()
                      if attempts >= MAX_FAILED_ATTEMPTS else None)
        cx.execute("UPDATE portal_credentials SET failed_attempts=?, locked_until=?, updated_at=? WHERE person_id=?",
                   (attempts, lock_until, _now().isoformat(), person_id))
        cx.commit()
        _record_event(cx, "login_failed", person_id=person_id, email=normalized,
                      ip=ip, user_agent=user_agent)
        return None
    if _hasher.check_needs_rehash(encoded):
        cx.execute("UPDATE portal_credentials SET password_hash=?, updated_at=? WHERE person_id=?",
                   (_hasher.hash(password), _now().isoformat(), person_id))
    cx.execute("UPDATE portal_credentials SET failed_attempts=0, locked_until=NULL, updated_at=? WHERE person_id=?",
               (_now().isoformat(), person_id))
    cx.commit()
    _record_event(cx, "login_succeeded", person_id=person_id, email=normalized,
                  ip=ip, user_agent=user_agent)
    return person_id


def create_password_reset(cx, person_id, email):
    _ensure_auth_tokens(cx)
    token = secrets.token_urlsafe(32)
    now = _now()
    cx.execute(
        "INSERT INTO auth_tokens (token_hash,email,purpose,extra,created_at,expires_at) VALUES (?,?,?,?,?,?)",
        (_hash_token(token), (email or "").lower(), RESET_PURPOSE,
         json.dumps({"person_id": person_id}), now.isoformat(),
         (now + timedelta(minutes=RESET_TTL_MIN)).isoformat()),
    )
    cx.commit()
    return token


def _live_reset(cx, token):
    _ensure_auth_tokens(cx)
    row = cx.execute(
        "SELECT extra,expires_at,consumed_at FROM auth_tokens WHERE token_hash=? AND purpose=?",
        (_hash_token(token), RESET_PURPOSE),
    ).fetchone()
    if not row or row[2] or _is_expired(row[1]):
        return None
    try:
        return int((json.loads(row[0] or "{}") or {}).get("person_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def validate_password_reset(cx, token):
    return _live_reset(cx, token)


def consume_password_reset(cx, token, password, *, ip="", user_agent=""):
    person_id = _live_reset(cx, token)
    if not person_id:
        return None
    ok, _ = validate_password(password)
    if not ok:
        return None
    cur = cx.execute(
        "UPDATE auth_tokens SET consumed_at=? WHERE token_hash=? AND purpose=? AND consumed_at IS NULL",
        (_now().isoformat(), _hash_token(token), RESET_PURPOSE),
    )
    cx.commit()
    if cur.rowcount != 1:
        return None
    row = cx.execute("SELECT email FROM people WHERE id=?", (person_id,)).fetchone()
    email = row[0] if row else ""
    set_password(cx, person_id, password, email=email, ip=ip, user_agent=user_agent)
    revoke_person_sessions(cx, person_id)
    return person_id


def revoke_session(cx, token):
    if not token:
        return False
    cur = cx.execute(
        "UPDATE auth_tokens SET consumed_at=? WHERE token_hash=? AND purpose='client_session' AND consumed_at IS NULL",
        (_now().isoformat(), _hash_token(token)),
    )
    cx.commit()
    return cur.rowcount == 1


def revoke_person_sessions(cx, person_id):
    _ensure_auth_tokens(cx)
    rows = cx.execute(
        "SELECT token_hash,extra FROM auth_tokens WHERE purpose='client_session' AND consumed_at IS NULL"
    ).fetchall()
    hashes = []
    for token_hash, extra in rows:
        try:
            if int((json.loads(extra or "{}") or {}).get("person_id")) == int(person_id):
                hashes.append(token_hash)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    now = _now().isoformat()
    for token_hash in hashes:
        cx.execute("UPDATE auth_tokens SET consumed_at=? WHERE token_hash=?", (now, token_hash))
    cx.commit()
    return len(hashes)
