"""Occurrence-aware live-event attendance evidence. Stdlib-only."""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS live_event_attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL, event_type TEXT NOT NULL, event_date_hst TEXT NOT NULL,
        zoom_meeting_id TEXT, zoom_meeting_uuid TEXT,
        email TEXT NOT NULL, display_name TEXT,
        first_join TEXT, last_leave TEXT, duration_minutes INTEGER DEFAULT 0,
        raw_join_count INTEGER DEFAULT 1, matched_ghl INTEGER DEFAULT 0,
        tagged_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(event_key, email))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS live_event_report_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL, event_type TEXT NOT NULL, event_date_hst TEXT NOT NULL,
        zoom_meeting_id TEXT, zoom_meeting_uuid TEXT,
        raw_participant_count INTEGER DEFAULT 0,
        human_attendee_count INTEGER DEFAULT 0,
        matched_tagged_count INTEGER DEFAULT 0,
        unmatched_count INTEGER DEFAULT 0, excluded_host_bot_count INTEGER DEFAULT 0,
        status TEXT NOT NULL, recorded_at TEXT NOT NULL,
        UNIQUE(event_key, zoom_meeting_uuid))""")
    cx.commit()


def record_attendee(cx, *, event_key, event_type, event_date_hst,
                    meeting_id, meeting_uuid, email, display_name="",
                    first_join="", last_leave="", duration_minutes=0,
                    raw_join_count=1, matched_ghl=False, tagged_at=""):
    """Idempotently store one deduplicated human attendee, keyed by email."""
    init_tables(cx)
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        raise ValueError("verified attendee email is required")
    now = _now()
    cx.execute(
        "INSERT INTO live_event_attendance (event_key,event_type,event_date_hst,"
        "zoom_meeting_id,zoom_meeting_uuid,email,display_name,first_join,last_leave,"
        "duration_minutes,raw_join_count,matched_ghl,tagged_at,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,email) DO UPDATE SET "
        "event_type=excluded.event_type,event_date_hst=excluded.event_date_hst,"
        "zoom_meeting_id=excluded.zoom_meeting_id,zoom_meeting_uuid=excluded.zoom_meeting_uuid,"
        "display_name=excluded.display_name,first_join=excluded.first_join,"
        "last_leave=excluded.last_leave,duration_minutes=excluded.duration_minutes,"
        "raw_join_count=excluded.raw_join_count,matched_ghl=excluded.matched_ghl,"
        "tagged_at=excluded.tagged_at,updated_at=excluded.updated_at",
        (event_key, event_type, event_date_hst, str(meeting_id or ""),
         str(meeting_uuid or ""), normalized, (display_name or "").strip(),
         first_join or "", last_leave or "", int(duration_minutes or 0),
         int(raw_join_count or 1), 1 if matched_ghl else 0, tagged_at or "", now, now))
    cx.commit()


def record_report_run(cx, *, event_key, event_type, event_date_hst,
                      meeting_id, meeting_uuid, raw_participant_count,
                      human_attendee_count, matched_tagged_count, unmatched_count,
                      excluded_host_bot_count, status):
    init_tables(cx)
    cx.execute(
        "INSERT INTO live_event_report_runs (event_key,event_type,event_date_hst,"
        "zoom_meeting_id,zoom_meeting_uuid,raw_participant_count,human_attendee_count,"
        "matched_tagged_count,unmatched_count,excluded_host_bot_count,status,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,zoom_meeting_uuid) "
        "DO UPDATE SET raw_participant_count=excluded.raw_participant_count,"
        "human_attendee_count=excluded.human_attendee_count,"
        "matched_tagged_count=excluded.matched_tagged_count,"
        "unmatched_count=excluded.unmatched_count,"
        "excluded_host_bot_count=excluded.excluded_host_bot_count,"
        "status=excluded.status,recorded_at=excluded.recorded_at",
        (event_key, event_type, event_date_hst, str(meeting_id or ""),
         str(meeting_uuid or ""), int(raw_participant_count or 0),
         int(human_attendee_count or 0), int(matched_tagged_count or 0),
         int(unmatched_count or 0), int(excluded_host_bot_count or 0),
         status or "needs_attention", _now()))
    cx.commit()
