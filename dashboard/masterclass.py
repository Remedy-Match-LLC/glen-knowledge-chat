"""MasterClass events + registrations. Stdlib-only; import without importing app."""
import sqlite3
from datetime import datetime, timezone

from dashboard import db, dbwrite

def _now():
    return datetime.now(timezone.utc).isoformat()


def _row_dict(cur, row):
    """Return a row mapping on SQLite and the PostgreSQL compatibility layer."""
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(zip((column[0] for column in cur.description), row))

def init_masterclass_tables(cx) -> None:
    cx.execute("""CREATE TABLE IF NOT EXISTS masterclass_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, description TEXT,
        start_ts TEXT, duration_min INTEGER DEFAULT 60,
        price_cents INTEGER DEFAULT 0, member_price_cents INTEGER DEFAULT 0,
        zoom_join_url TEXT, zoom_meeting_id TEXT, zoom_registration_url TEXT,
        zoom_occurrence_id TEXT, registration_required INTEGER DEFAULT 0,
        created_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS masterclass_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, email TEXT, name TEXT,
        is_member INTEGER, amount_cents INTEGER, paid INTEGER DEFAULT 0, created_at TEXT,
        zoom_registrant_id TEXT, zoom_join_url TEXT, zoom_occurrence_id TEXT,
        zoom_registered_at TEXT, zoom_registration_error TEXT,
        referrer_slug TEXT, referred_at TEXT,
        UNIQUE(event_id, email))""")
    event_columns = {
        "zoom_registration_url": "TEXT", "zoom_occurrence_id": "TEXT",
        "registration_required": "INTEGER DEFAULT 0",
    }
    registration_columns = {
        "zoom_registrant_id": "TEXT", "zoom_join_url": "TEXT",
        "zoom_occurrence_id": "TEXT", "zoom_registered_at": "TEXT",
        "zoom_registration_error": "TEXT", "referrer_slug": "TEXT",
        "referred_at": "TEXT",
    }
    for column, declaration in event_columns.items():
        if not db.column_exists(cx, "masterclass_events", column):
            cx.execute(f"ALTER TABLE masterclass_events ADD COLUMN {column} {declaration}")
    for column, declaration in registration_columns.items():
        if not db.column_exists(cx, "masterclass_registrations", column):
            cx.execute(f"ALTER TABLE masterclass_registrations ADD COLUMN {column} {declaration}")
    cx.commit()

def create_event(cx, *, topic, description, start_ts, duration_min,
                 price_cents, member_price_cents) -> int:
    new_id = dbwrite.insert_returning_id(cx,
        "INSERT INTO masterclass_events (topic, description, start_ts, duration_min, "
        "price_cents, member_price_cents, created_at) VALUES (?,?,?,?,?,?,?)",
        ((topic or "").strip(), (description or "").strip(), start_ts, int(duration_min),
         int(price_cents), int(member_price_cents), _now()))
    cx.commit()
    return new_id

def get_event(cx, event_id):
    cur = cx.execute("SELECT * FROM masterclass_events WHERE id=?", (event_id,))
    row = cur.fetchone()
    return _row_dict(cur, row) if row is not None else None

def set_zoom(cx, event_id, join_url, meeting_id, *, registration_url="",
             occurrence_id="") -> None:
    cx.execute("UPDATE masterclass_events SET zoom_join_url=?, zoom_meeting_id=?, "
               "zoom_registration_url=?, zoom_occurrence_id=?, registration_required=1 "
               "WHERE id=?", (join_url, meeting_id, registration_url,
                               occurrence_id, event_id))
    cx.commit()

def price_for(event, is_member) -> int:
    return int(event["member_price_cents"] if is_member else event["price_cents"])

def register(cx, event_id, email, name, is_member, amount_cents, *, paid) -> None:
    email = (email or "").strip().lower()
    cx.execute("INSERT INTO masterclass_registrations (event_id, email, name, is_member, "
               "amount_cents, paid, created_at) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(event_id, email) DO UPDATE SET name=excluded.name, "
               "is_member=excluded.is_member, amount_cents=excluded.amount_cents, "
               "paid=excluded.paid",
               (event_id, email, (name or "").strip(), 1 if is_member else 0,
                int(amount_cents), 1 if paid else 0, _now()))
    cx.commit()

def mark_paid(cx, event_id, email) -> None:
    cx.execute("UPDATE masterclass_registrations SET paid=1 WHERE event_id=? AND lower(email)=?",
               (event_id, (email or "").strip().lower()))
    cx.commit()

def is_registered(cx, event_id, email) -> bool:
    r = cx.execute("SELECT 1 FROM masterclass_registrations WHERE event_id=? AND lower(email)=? "
                   "AND paid=1", (event_id, (email or "").strip().lower())).fetchone()
    return r is not None


def get_registration(cx, event_id, email):
    cur = cx.execute("SELECT * FROM masterclass_registrations "
                     "WHERE event_id=? AND lower(email)=?",
                     (event_id, (email or "").strip().lower()))
    row = cur.fetchone()
    return _row_dict(cur, row) if row is not None else None


def set_referrer_if_empty(cx, event_id, email, referrer_slug) -> bool:
    """Persist the first approved ambassador attributed to this event RSVP."""
    slug = (referrer_slug or "").strip()
    if not slug:
        return False
    cur = cx.execute(
        "UPDATE masterclass_registrations SET referrer_slug=?, referred_at=? "
        "WHERE event_id=? AND lower(email)=? "
        "AND COALESCE(referrer_slug,'')=''",
        (slug, _now(), event_id, (email or "").strip().lower()))
    cx.commit()
    return cur.rowcount > 0


def set_zoom_registration(cx, event_id, email, *, registrant_id, join_url,
                          occurrence_id="") -> None:
    cx.execute("UPDATE masterclass_registrations SET zoom_registrant_id=?, "
               "zoom_join_url=?, zoom_occurrence_id=?, zoom_registered_at=?, "
               "zoom_registration_error='' WHERE event_id=? AND lower(email)=?",
               (registrant_id or "", join_url or "", occurrence_id or "", _now(),
                event_id, (email or "").strip().lower()))
    cx.commit()


def set_zoom_registration_error(cx, event_id, email, error) -> None:
    cx.execute("UPDATE masterclass_registrations SET zoom_registration_error=? "
               "WHERE event_id=? AND lower(email)=?",
               ((error or "zoom_registration_failed")[:500], event_id,
                (email or "").strip().lower()))
    cx.commit()
