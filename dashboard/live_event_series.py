"""Stable Zoom series and identity-bound series registrations."""

from datetime import datetime, timezone

def _now():
    return datetime.now(timezone.utc).isoformat()


def _row(cur, value):
    if value is None:
        return None
    if hasattr(value, "keys"):
        return {key: value[key] for key in value.keys()}
    return dict(zip((column[0] for column in cur.description), value))


def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS live_event_series (
        series_key TEXT PRIMARY KEY, title TEXT NOT NULL,
        zoom_meeting_id TEXT NOT NULL, zoom_registration_url TEXT,
        registration_required INTEGER DEFAULT 1,
        recurring INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS live_event_series_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, series_key TEXT NOT NULL,
        email TEXT NOT NULL, zoom_registrant_id TEXT, zoom_join_url TEXT,
        registered_at TEXT, updated_at TEXT,
        UNIQUE(series_key,email))""")
    cx.commit()


def get_series(cx, series_key):
    init_tables(cx)
    cur = cx.execute("SELECT * FROM live_event_series WHERE series_key=?", (series_key,))
    return _row(cur, cur.fetchone())


def get_series_for_meeting(cx, meeting_id):
    init_tables(cx)
    cur = cx.execute("SELECT * FROM live_event_series WHERE zoom_meeting_id=?",
                     (str(meeting_id or "").strip(),))
    return _row(cur, cur.fetchone())


def upsert_series(cx, series_key, title, meeting_id, registration_url):
    init_tables(cx)
    now = _now()
    cx.execute(
        "INSERT INTO live_event_series (series_key,title,zoom_meeting_id,"
        "zoom_registration_url,registration_required,recurring,created_at,updated_at) "
        "VALUES (?,?,?,?,1,1,?,?) ON CONFLICT(series_key) DO UPDATE SET "
        "title=excluded.title,zoom_meeting_id=excluded.zoom_meeting_id,"
        "zoom_registration_url=excluded.zoom_registration_url,"
        "registration_required=1,recurring=1,updated_at=excluded.updated_at",
        (series_key, title, str(meeting_id), registration_url or "", now, now))
    cx.commit()


def get_registration(cx, series_key, email):
    init_tables(cx)
    cur = cx.execute(
        "SELECT * FROM live_event_series_registrations "
        "WHERE series_key=? AND lower(email)=?",
        (series_key, (email or "").strip().lower()))
    return _row(cur, cur.fetchone())


def set_registration(cx, series_key, email, registrant_id, join_url):
    init_tables(cx)
    now = _now()
    cx.execute(
        "INSERT INTO live_event_series_registrations "
        "(series_key,email,zoom_registrant_id,zoom_join_url,registered_at,updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(series_key,email) DO UPDATE SET "
        "zoom_registrant_id=excluded.zoom_registrant_id,"
        "zoom_join_url=excluded.zoom_join_url,updated_at=excluded.updated_at",
        (series_key, (email or "").strip().lower(), registrant_id or "",
         join_url or "", now, now))
    cx.commit()
