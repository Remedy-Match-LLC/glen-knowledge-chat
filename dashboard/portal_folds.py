"""Card fold state for the client portal, one record per viewer per portal.

Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md

A viewer is 'client', 'staff:master' or 'staff:user:<workspace_users.id>'. The route
decides which; nothing here trusts the browser to say who it is. Keyed by the portal's
email because tokens get reissued and the email does not.
"""
import json
from datetime import datetime, timezone

MAX_BYTES = 64 * 1024
MAX_CARDS = 500
_MAX_ID = 120
_MAX_DOOR = 40
_MAX_DOORS = 50


class TooLarge(ValueError):
    pass


def init_table(cx) -> None:
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_fold_state (
            portal_email TEXT NOT NULL,
            viewer       TEXT NOT NULL,
            state_json   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (portal_email, viewer)
        )
        """
    )
    cx.commit()


def empty() -> dict:
    return {"cards": {}, "seen": [], "before_fold_all": {}}


def _card_map(v) -> dict:
    out = {}
    if not isinstance(v, dict):
        return out
    for k, folded in v.items():
        if isinstance(k, str) and 0 < len(k) <= _MAX_ID and isinstance(folded, bool):
            out[k] = folded
            if len(out) >= MAX_CARDS:
                break
    return out


def _door_ok(d) -> bool:
    return isinstance(d, str) and 0 < len(d) <= _MAX_DOOR


def clean(state) -> dict:
    """Keep only the three known keys, each in its expected shape."""
    if not isinstance(state, dict):
        return empty()
    seen_raw = state.get("seen")
    seen = []
    if isinstance(seen_raw, list):
        for d in seen_raw:
            if _door_ok(d) and d not in seen:
                seen.append(d)
    seen = seen[:_MAX_DOORS]
    bfa = {}
    bfa_raw = state.get("before_fold_all")
    if isinstance(bfa_raw, dict):
        for door, cards in bfa_raw.items():
            if _door_ok(door) and len(bfa) < _MAX_DOORS:
                bfa[door] = _card_map(cards)
    return {"cards": _card_map(state.get("cards")), "seen": seen, "before_fold_all": bfa}


def _norm(email) -> str:
    return (email or "").strip().lower()


def get(cx, portal_email, viewer) -> dict:
    row = cx.execute(
        "SELECT state_json FROM portal_fold_state WHERE portal_email = ? AND viewer = ?",
        (_norm(portal_email), viewer)).fetchone()
    if not row:
        return empty()
    try:
        return clean(json.loads(row[0]))
    except (TypeError, ValueError):
        return empty()


def put(cx, portal_email, viewer, state) -> dict:
    st = clean(state)
    body = json.dumps(st, separators=(",", ":"), sort_keys=True)
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise TooLarge(f"fold record is {len(body)} bytes")
    cx.execute(
        "INSERT INTO portal_fold_state (portal_email, viewer, state_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (portal_email, viewer) DO UPDATE SET "
        "state_json = excluded.state_json, updated_at = excluded.updated_at",
        (_norm(portal_email), viewer, body, datetime.now(timezone.utc).isoformat()))
    cx.commit()
    return st
