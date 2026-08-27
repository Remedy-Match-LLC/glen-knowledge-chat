"""Client photo store — one photo per client, keyed by lowercased email.

Central store for Slice 1 of the client-photos feature: the Biofield Intake page
and console/reveal surfaces read a client's photo by email; FMP-export upload,
portal self-upload, and GHL pull all write here. Persistence only — no HTTP, no
rendering. See docs/superpowers/specs/2026-07-14-client-photos-design.md.
"""
import datetime

from dashboard import db


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _norm(email):
    return (email or "").strip().lower()


_RANK = {"portal-self": 4, "console": 3, "fmp-intake-upload": 3, "fmp": 2, "ghl": 1}


def _rank(source):
    return _RANK.get((source or "").strip().lower(), 0)


def init_table(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS client_photos("
        "email TEXT PRIMARY KEY, image_blob BLOB, content_type TEXT, "
        "source TEXT, updated_at TEXT, focus_x REAL NOT NULL DEFAULT 50, "
        "focus_y REAL NOT NULL DEFAULT 42, zoom REAL NOT NULL DEFAULT 1)")
    # Existing installations predate nondestructive avatar framing.
    for column, declaration in (
            ("focus_x", "REAL NOT NULL DEFAULT 50"),
            ("focus_y", "REAL NOT NULL DEFAULT 42"),
            ("zoom", "REAL NOT NULL DEFAULT 1")):
        if not db.column_exists(cx, "client_photos", column):
            cx.execute(f"ALTER TABLE client_photos ADD COLUMN {column} {declaration}")


def would_skip_precedence(cx, email, source):
    """True if an existing photo's source outranks `source` (a force=False write would skip)."""
    e = _norm(email)
    if not e:
        return False
    init_table(cx)
    row = cx.execute("SELECT source FROM client_photos WHERE email=?", (e,)).fetchone()
    return bool(row and _rank(source) < _rank(row[0]))


def put(cx, email, blob, content_type, source="upload", force=True):
    """Upsert a client's photo. force=True (default) = last-write-wins (all existing
    callers). force=False = skip when an existing photo's source outranks `source`
    (a bulk 'fmp' write must not clobber a client's own 'portal-self' upload).
    Returns the normalized email written, or None (no email/blob, or precedence skip)."""
    e = _norm(email)
    if not e or not blob:
        return None
    init_table(cx)
    if not force and would_skip_precedence(cx, e, source):
        return None
    cx.execute(
        "INSERT INTO client_photos(email, image_blob, content_type, source, updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET "
        "image_blob=excluded.image_blob, content_type=excluded.content_type, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (e, blob, (content_type or "image/jpeg"), source, _now()))
    cx.commit()
    return e


def get(cx, email):
    """Return the photo bytes plus nondestructive face framing, or None."""
    e = _norm(email)
    if not e:
        return None
    init_table(cx)
    row = cx.execute(
        "SELECT image_blob, content_type, focus_x, focus_y, zoom, source, updated_at "
        "FROM client_photos WHERE email=?", (e,)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return {"blob": row[0], "content_type": row[1] or "image/jpeg",
            "focus_x": float(row[2] if row[2] is not None else 50),
            "focus_y": float(row[3] if row[3] is not None else 42),
            "zoom": float(row[4] if row[4] is not None else 1),
            "source": row[5] or "", "updated_at": row[6] or ""}


def set_framing(cx, email, focus_x, focus_y, zoom):
    """Save a client's chosen crop without modifying the original image."""
    e = _norm(email)
    if not e:
        return False
    init_table(cx)
    focus_x = max(0.0, min(100.0, float(focus_x)))
    focus_y = max(0.0, min(100.0, float(focus_y)))
    zoom = max(1.0, min(3.0, float(zoom)))
    cur = cx.execute(
        "UPDATE client_photos SET focus_x=?, focus_y=?, zoom=?, updated_at=? "
        "WHERE email=?", (focus_x, focus_y, zoom, _now(), e))
    cx.commit()
    return cur.rowcount > 0


def has(cx, email):
    return get(cx, email) is not None
