"""Upcoming member-facing events for the Healing Oasis portal.

Titles and times are visible to every portal holder. Private join destinations
are only added after the caller establishes entitlement server-side.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _row_dict(cur, row):
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


def init_registration_table(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_event_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL,
        email TEXT NOT NULL, registered_at TEXT NOT NULL,
        UNIQUE(event_key, email))""")
    cx.commit()


def register_group(cx, event_key, email):
    init_registration_table(cx)
    cx.execute("INSERT OR IGNORE INTO portal_event_registrations "
               "(event_key,email,registered_at) VALUES (?,?,?)",
               (event_key, (email or "").strip().lower(), _now_iso()))
    cx.commit()


def _registered_keys(cx, email):
    keys = set()
    try:
        init_registration_table(cx)
        keys.update(r[0] for r in cx.execute(
            "SELECT event_key FROM portal_event_registrations WHERE lower(email)=?",
            ((email or "").strip().lower(),)).fetchall())
    except Exception:
        pass
    try:
        keys.update(f"masterclass-{r[0]}" for r in cx.execute(
            "SELECT event_id FROM masterclass_registrations "
            "WHERE lower(email)=? AND paid=1", ((email or "").strip().lower(),)).fetchall())
    except Exception:
        pass
    return keys


def build_block(cx, *, email="", group_coaching_entitled=False,
                now_iso=None, upgrade_url="/membership"):
    now_iso = (now_iso or _now_iso()).strip()
    events = []
    registered_keys = _registered_keys(cx, email)

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
            pass

    try:
        cur = cx.execute(
            "SELECT id, topic, description, start_ts, duration_min "
            "FROM masterclass_events WHERE start_ts>=? ORDER BY start_ts ASC LIMIT 50",
            (now_iso,))
        for row in cur.fetchall():
            item = _row_dict(cur, row)
            key = f"masterclass-{item['id']}"
            events.append({"id": key, "type": "masterclass",
                "title": item.get("topic") or "MasterClass",
                "description": item.get("description") or "",
                "start": _zoned_iso(item.get("start_ts")),
                "duration_min": int(item.get("duration_min") or 60),
                "members_only": False, "locked": False,
                "action_url": f"/masterclass/{item['id']}",
                "action_label": "View & register", "registered": key in registered_keys})
    except Exception:
        pass

    try:
        cur = cx.execute(
            'SELECT id, summary, start, "end", location FROM calendar_events '
            "WHERE status='visible' AND start>=? "
            "AND (lower(summary) LIKE '%group coaching%' "
            "OR lower(calendar_name) LIKE '%group coaching%') "
            "ORDER BY start ASC LIMIT 50", (now_iso,))
        for row in cur.fetchall():
            item = _row_dict(cur, row)
            entitled = bool(group_coaching_entitled)
            location = (item.get("location") or "").strip()
            join_url = location if location.lower().startswith(("https://", "http://")) else ""
            key = f"group-{item['id']}"
            events.append({"id": key, "type": "group_coaching",
                "title": item.get("summary") or "Group Coaching",
                "description": "Live group coaching with Dr. Glen.",
                "start": _zoned_iso(item.get("start")), "end": _zoned_iso(item.get("end")),
                "members_only": True, "locked": not entitled,
                "action_url": join_url if entitled else upgrade_url,
                "action_label": (("Join session" if join_url else "Access details coming soon")
                                 if entitled else "Upgrade to access"),
                "registered": key in registered_keys})
    except Exception:
        pass

    events.sort(key=lambda e: (e.get("start") or "", e.get("title") or ""))
    return {"enabled": True, "events": events,
            "group_coaching_entitled": bool(group_coaching_entitled),
            "upgrade_url": upgrade_url}
