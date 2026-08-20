"""Upcoming member-facing events for the Healing Oasis portal.

Titles and times are visible to every portal holder. Private join destinations
are only added after the caller establishes entitlement server-side.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dashboard import db


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _row_dict(cur, row):
    # SQLite exposes cursor.description + tuple rows. The PostgreSQL adapter returns
    # mapping rows and intentionally does not emulate cursor.description.
    if hasattr(row, "keys"):
        return dict(row)
    return dict(zip((c[0] for c in cur.description), row))


def _zoned_iso(value):
    """Attach Hawai'i time to naive authoring values for browser conversion."""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Pacific/Honolulu"))
        return parsed.isoformat()
    except (ValueError, TypeError):
        return raw


def _recover_optional_query(cx):
    """Clear PostgreSQL's failed-transaction state after an optional read fails."""
    try:
        cx.rollback()
    except Exception:
        pass

def init_registration_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_event_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL,
        email TEXT NOT NULL, registered_at TEXT NOT NULL,
        zoom_meeting_id TEXT, zoom_occurrence_id TEXT,
        zoom_registrant_id TEXT, zoom_join_url TEXT,
        zoom_registration_error TEXT,
        UNIQUE(event_key, email))""")
    columns = {
        "zoom_meeting_id": "TEXT", "zoom_occurrence_id": "TEXT",
        "zoom_registrant_id": "TEXT", "zoom_join_url": "TEXT",
        "zoom_registration_error": "TEXT",
    }
    for column, declaration in columns.items():
        if not db.column_exists(cx, "portal_event_registrations", column):
            cx.execute(f"ALTER TABLE portal_event_registrations ADD COLUMN {column} {declaration}")
    cx.commit()


def register_group(cx, event_key, email, *, meeting_id="", occurrence_id="",
                   registrant_id="", join_url="", error=""):
    init_registration_table(cx)
    cx.execute("INSERT INTO portal_event_registrations "
               "(event_key,email,registered_at,zoom_meeting_id,zoom_occurrence_id,"
               "zoom_registrant_id,zoom_join_url,zoom_registration_error) "
               "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_key,email) DO UPDATE SET "
               "zoom_meeting_id=excluded.zoom_meeting_id,"
               "zoom_occurrence_id=excluded.zoom_occurrence_id,"
               "zoom_registrant_id=excluded.zoom_registrant_id,"
               "zoom_join_url=excluded.zoom_join_url,"
               "zoom_registration_error=excluded.zoom_registration_error",
               (event_key, (email or "").strip().lower(), _now_iso(),
                meeting_id or "", occurrence_id or "", registrant_id or "",
                join_url or "", (error or "")[:500]))
    cx.commit()


def get_registration(cx, event_key, email):
    init_registration_table(cx)
    cur = cx.execute("SELECT * FROM portal_event_registrations "
                     "WHERE event_key=? AND lower(email)=?",
                     (event_key, (email or "").strip().lower()))
    row = cur.fetchone()
    if row is None:
        return None
    return _row_dict(cur, row)


def _registration_records(cx, email):
    records = {}
    try:
        init_registration_table(cx)
        for row in cx.execute(
                "SELECT event_key,zoom_join_url FROM portal_event_registrations "
                "WHERE lower(email)=?", ((email or "").strip().lower(),)).fetchall():
            records[row[0]] = {"join_url": row[1] or ""}
    except Exception:
        _recover_optional_query(cx)

    try:
        for row in cx.execute(
            "SELECT event_id,zoom_join_url FROM masterclass_registrations "
            "WHERE lower(email)=? AND paid=1",
                ((email or "").strip().lower(),)).fetchall():
            records[f"masterclass-{row[0]}"] = {"join_url": row[1] or ""}
    except Exception:
        _recover_optional_query(cx)
    return records


def _approved_ambassador_slug(cx, email):
    """Return the portal client's approved affiliate slug, if one exists."""
    if not email:
        return ""
    try:
        row = cx.execute(
            "SELECT slug FROM affiliate_signups WHERE lower(email)=? "
            "AND status='approved' LIMIT 1",
            ((email or "").strip().lower(),)).fetchone()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def build_block(cx, *, email="", group_coaching_entitled=False,
                now_iso=None, upgrade_url="/membership"):
    now_iso = (now_iso or _now_iso()).strip()
    events = []
    registrations = _registration_records(cx, email)
    ambassador_slug = _approved_ambassador_slug(cx, email)

    if email:
        try:
            cur = cx.execute(
                "SELECT id, session_type, practitioner, medium, start_ts, end_ts, prepaid "
                "FROM evox_bookings WHERE lower(email)=? AND status='booked' AND start_ts>=? "
                "ORDER BY start_ts ASC LIMIT 50", ((email or "").strip().lower(), now_iso))
            labels = {"biofield-consult": "Biofield Analysis Consultation",
                      "evox": "EVOX Session", "onboarding": "Welcome Call",
                      "triage": "Discovery Call"}
            for row in cur.fetchall():
                item = _row_dict(cur, row)
                kind = item.get("session_type") or "appointment"
                who = "Dr. Glen" if item.get("practitioner") == "glen" else "Rae"
                events.append({"id": f"appointment-{item['id']}", "type": "appointment",
                    "title": labels.get(kind, "Private Appointment"),
                    "description": f"Private {item.get('medium') or 'session'} appointment with {who}.",
                    "start": _zoned_iso(item.get("start_ts")),
                    "end": _zoned_iso(item.get("end_ts")),
                    "members_only": False, "locked": False, "registered": True,
                    "action_url": "", "action_label": "Confirmed",
                    "prepaid": bool(item.get("prepaid"))})
        except Exception:
            _recover_optional_query(cx)

    try:
        cur = cx.execute(
            "SELECT id, topic, description, start_ts, duration_min, price_cents "
            "FROM masterclass_events WHERE start_ts>=? ORDER BY start_ts ASC LIMIT 50",
            (now_iso,))
        for row in cur.fetchall():
            item = _row_dict(cur, row)
            key = f"masterclass-{item['id']}"
            registration = registrations.get(key) or {}
            registered = key in registrations
            events.append({"id": key, "type": "masterclass",
                "title": item.get("topic") or "MasterClass",
                "description": item.get("description") or "",
                "start": _zoned_iso(item.get("start_ts")),
                "duration_min": int(item.get("duration_min") or 60),
                "members_only": False, "locked": False,
                "action_url": ((registration.get("join_url") or "") if registered
                               else f"/masterclass/{item['id']}"),
                "action_label": ("Join session" if registration.get("join_url")
                                 else "View & register"),
                "registered": registered,
                "share_url": (f"/masterclass/{item['id']}?ref={ambassador_slug}"
                              if ambassador_slug and int(item.get("price_cents") or 0) == 0
                              else "")})
    except Exception:
        _recover_optional_query(cx)

    try:
        cur = cx.execute(
            'SELECT id, summary, start, "end", location FROM calendar_events '
            "WHERE status='visible' AND COALESCE(NULLIF(\"end\", ''), start)>=? "
            "AND (lower(summary) LIKE '%group coaching%' "
            "OR lower(calendar_name) LIKE '%group coaching%') "
            "ORDER BY start ASC LIMIT 50", (now_iso,))
        for row in cur.fetchall():
            item = _row_dict(cur, row)
            entitled = bool(group_coaching_entitled)
            key = f"group-{item['id']}"
            registration = registrations.get(key) or {}
            join_url = registration.get("join_url") or ""
            events.append({"id": key, "type": "group_coaching",
                "title": item.get("summary") or "Group Coaching",
                "description": "Live group coaching with Dr. Glen.",
                "start": _zoned_iso(item.get("start")), "end": _zoned_iso(item.get("end")),
                "members_only": True, "locked": not entitled,
                "action_url": (join_url if entitled else upgrade_url),
                "action_label": (("Join session" if join_url else "Access details coming soon")
                                 if entitled else "Upgrade to access"),
                "registered": key in registrations})
    except Exception:
        _recover_optional_query(cx)

    # Do not manufacture future occurrences from an old shared Zoom link. Each
    # weekly event must be authored with its own meeting/occurrence identity so
    # registration and attendance can be reconciled without ambiguity.

    events.sort(key=lambda e: (e.get("start") or "", e.get("title") or ""))
    return {"enabled": True, "events": events,
            "group_coaching_entitled": bool(group_coaching_entitled),
            "upgrade_url": upgrade_url}
