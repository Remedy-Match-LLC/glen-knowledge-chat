"""Upcoming member-facing events for the Healing Oasis portal.

Titles and times are visible to every portal holder. Private join destinations
are only added after the caller establishes entitlement server-side.
"""
from datetime import datetime, timedelta, timezone
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


def _weekly_starts(template_start, now_iso, count=8):
    """Return the next weekly local-time occurrences after ``now_iso``.

    Zoom uses recurring meeting links, while our database historically stored only
    one dated occurrence. Expanding at read time keeps upcoming community activities
    visible without manufacturing new Zoom meetings or requiring weekly data entry.
    """
    template = datetime.fromisoformat((template_start or "").replace("Z", "+00:00"))
    now = datetime.fromisoformat((now_iso or "").replace("Z", "+00:00"))
    if template.tzinfo:
        template = template.astimezone(ZoneInfo("Pacific/Honolulu")).replace(tzinfo=None)
    if now.tzinfo:
        now = now.astimezone(ZoneInfo("Pacific/Honolulu")).replace(tzinfo=None)
    candidate = template
    if candidate <= now:
        candidate += timedelta(weeks=((now - candidate).days // 7) + 1)
    return [candidate + timedelta(weeks=i) for i in range(count)]


def _occurrence_key(prefix, row_id, start):
    return f"{prefix}-{row_id}-{start.strftime('%Y%m%d')}"


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
        _recover_optional_query(cx)

    try:
        keys.update(f"masterclass-{r[0]}" for r in cx.execute(
            "SELECT event_id FROM masterclass_registrations "
            "WHERE lower(email)=? AND paid=1", ((email or "").strip().lower(),)).fetchall())
    except Exception:
        _recover_optional_query(cx)
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
            _recover_optional_query(cx)

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
        _recover_optional_query(cx)

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
        _recover_optional_query(cx)

    # These are recurring community activities whose Zoom links remain stable from
    # week to week. Materialize the next eight Wednesdays from the latest authored
    # occurrence. Concrete future rows win for matching start times.
    concrete_masterclass_starts = {e.get("start", "")[:19] for e in events
                                   if e.get("type") == "masterclass"}
    concrete_group_starts = {e.get("start", "")[:19] for e in events
                             if e.get("type") == "group_coaching"}
    try:
        if not db.column_exists(cx, "masterclass_events", "id"):
            raise LookupError("masterclass_events unavailable")
        cur = cx.execute(
            "SELECT id, topic, description, start_ts, duration_min "
            "FROM masterclass_events WHERE lower(topic) LIKE '%wellness whispering%' "
            "ORDER BY start_ts DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            item = _row_dict(cur, row)
            base_key = f"masterclass-{item['id']}"
            for start in _weekly_starts(item["start_ts"], now_iso):
                raw = start.isoformat(timespec="seconds")
                if raw in concrete_masterclass_starts:
                    continue
                events.append({"id": _occurrence_key("masterclass", item["id"], start),
                    "type": "masterclass", "title": item.get("topic") or "MasterClass",
                    "description": item.get("description") or "", "start": _zoned_iso(raw),
                    "duration_min": int(item.get("duration_min") or 60),
                    "members_only": False, "locked": False,
                    "action_url": f"/masterclass/{item['id']}",
                    "action_label": "View & register",
                    "registered": base_key in registered_keys})
    except Exception:
        _recover_optional_query(cx)

    try:
        if not db.column_exists(cx, "calendar_events", "id"):
            raise LookupError("calendar_events unavailable")
        cur = cx.execute(
            'SELECT id, summary, start, "end", location FROM calendar_events '
            "WHERE status='visible' AND (lower(summary) LIKE '%group coaching%' "
            "OR lower(calendar_name) LIKE '%group coaching%') "
            "ORDER BY start DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            item = _row_dict(cur, row)
            template_start = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
            template_end = datetime.fromisoformat(item["end"].replace("Z", "+00:00"))
            duration = template_end - template_start
            entitled = bool(group_coaching_entitled)
            location = (item.get("location") or "").strip()
            join_url = location if location.lower().startswith(("https://", "http://")) else ""
            for start in _weekly_starts(item["start"], now_iso):
                raw = start.isoformat(timespec="seconds")
                if raw in concrete_group_starts:
                    continue
                key = _occurrence_key("group", item["id"], start)
                events.append({"id": key, "type": "group_coaching",
                    "title": item.get("summary") or "Group Coaching",
                    "description": "Live group coaching with Dr. Glen.",
                    "start": _zoned_iso(raw),
                    "end": _zoned_iso((start + duration).isoformat(timespec="seconds")),
                    "members_only": True, "locked": not entitled,
                    "action_url": join_url if entitled else upgrade_url,
                    "action_label": (("Join session" if join_url else "Access details coming soon")
                                     if entitled else "Upgrade to access"),
                    "registered": key in registered_keys})
    except Exception:
        _recover_optional_query(cx)

    events.sort(key=lambda e: (e.get("start") or "", e.get("title") or ""))
    return {"enabled": True, "events": events,
            "group_coaching_entitled": bool(group_coaching_entitled),
            "upgrade_url": upgrade_url}
