"""Upcoming member-facing events for the Healing Oasis portal.

Titles and times are visible to every portal holder. Private join destinations
are only added after the caller establishes entitlement server-side.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard import db


def _now_iso():
    # Portal-authored event timestamps are stored as naive Hawai'i wall time.
    # Comparing them to naive UTC hides every event ten hours too early.
    return (datetime.now(ZoneInfo("Pacific/Honolulu"))
            .replace(tzinfo=None).isoformat(timespec="seconds"))


def _row_dict(cur, row):
    # SQLite exposes cursor.description + tuple rows. The PostgreSQL adapter returns
    # mapping rows and intentionally does not emulate cursor.description.
    if hasattr(row, "keys"):
        return dict(row)
    return dict(zip((c[0] for c in cur.description), row))


# Rae's and Glen's own booking flows write start_ts as naive Hawai'i wall
# clock, and the two names this view used to hardcode were theirs. Both stay
# true for them, so their rows are answered from here without a lookup: a
# Supabase outage must not blank the appointments that worked before any
# practitioner existed.
_LEGACY_TZ = "Pacific/Honolulu"
_LEGACY_PRACTITIONERS = {"glen": "Dr. Glen", "rae": "Rae"}


def _zoned_iso(value, tz_name=_LEGACY_TZ):
    """Attach a zone to a naive authoring value for browser conversion.

    Defaults to Hawai'i because that is what portal-authored events genuinely
    ARE: MasterClass and group-coaching times are typed on Glen's own clock.
    Only an evox_bookings row can carry another practitioner's zone, so only
    that caller passes one. `tz_name` is always a zone that has already been
    resolved through ZoneInfo by _practitioner_identity; a row whose zone
    could not be resolved never reaches here, it is omitted.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
        return parsed.isoformat()
    except (ValueError, TypeError, KeyError):
        return raw


def _practitioner_name(pid):
    """The practitioner's own display name, or None on any failure.

    The same read the reminder cron makes, so one person is called one name in
    her client's inbox and in her client's portal. Its wrapper lives in app.py
    and this module must not import app, so the underlying helper is called
    directly rather than the read being reinvented.

    None is a real answer here and it means OMIT the booking. It must never be
    turned into a default: a default name in this view is another practitioner.
    """
    try:
        from db_supabase import supabase_cursor
        with supabase_cursor() as cur:
            cur.execute("SELECT name FROM practitioners WHERE id=%s", (str(pid),))
            row = cur.fetchone()
        return (row["name"] or "").strip() or None if row else None
    except Exception:
        return None


def _practitioner_identity(cx, who, cache):
    """(display name, IANA zone) for a booking's practitioner, or None.

    None means the row cannot be rendered truthfully and must be dropped. There
    is no safe fallback on either half: a missing name would show one
    practitioner's client another practitioner's name, and a missing zone would
    show a real appointment at an hour that is not its own, in an authenticated
    view that also says Confirmed. That is exactly the harm the old
    `practitioner IN ('rae','glen')` filter existed to prevent, so the fail-
    closed behaviour it gave us is kept after the filter itself is gone.

    `cache` is per build_block call, i.e. per client page load. A client with
    several bookings from one practitioner costs one Supabase round trip and
    one config read, not one of each per booking. A negative result is cached
    too, so an unresolvable practitioner is not retried per row either.
    """
    who = (who or "").strip()
    if who in cache:
        return cache[who]
    identity = None
    if who in _LEGACY_PRACTITIONERS:
        identity = (_LEGACY_PRACTITIONERS[who], _LEGACY_TZ, {})
    elif who:
        name = _practitioner_name(who)
        # get_config is the one reader of her stored zone, and it fails closed
        # to None for both "no row" and "a row that cannot be read back".
        #
        # It SWALLOWS the db error rather than raising it, which is the trap:
        # on PostgreSQL a failed read leaves the transaction aborted, and
        # because nothing was raised, the `except` below never fires. The next
        # section's query then dies with InFailedSqlTransaction and that whole
        # block goes missing from the client's page. So a falsy result is
        # treated as needing recovery too, not just an exception.
        own_labels = {}
        try:
            from dashboard import practitioner_booking as _pb
            _cfg = _pb.get_config(cx, who)
            if not _cfg:
                # Swallowed-error case: recover before the caller's next query.
                _recover_optional_query(cx)
                _cfg = {}
            tz_name = (_cfg.get("timezone") or "").strip()
            # Her own session labels, taken from the config THIS call already
            # read. The module-level `labels` map only knows Rae's and Glen's
            # four types, so without this a client sees her Biofield Analysis
            # rendered as the generic "Private Appointment" -- her own words
            # are on the row and cost nothing extra to carry.
            own_labels = {
                str(st.get("slug") or ""): str(st.get("label") or "").strip()
                for st in (_cfg.get("session_types") or [])
                if st.get("slug") and str(st.get("label") or "").strip()
            }
        except Exception:
            _recover_optional_query(cx)
            tz_name = ""
        try:
            ZoneInfo(tz_name)
        except Exception:
            tz_name = ""
        if name and tz_name:
            identity = (name, tz_name, own_labels)
    cache[who] = identity
    return identity


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
            # This query was scoped `practitioner IN ('rae','glen')` because the
            # view below could not render anyone else: it named every row
            # "Dr. Glen" or "Rae" and stamped every row Pacific/Honolulu, so a
            # Healing Oasis client who had separately booked a different
            # practitioner would have seen that appointment under Rae's name at
            # a Honolulu hour in their authenticated portal, marked Confirmed.
            # The filter was the only thing holding that shut.
            #
            # It is safe to drop now, and ONLY now, because the loop below
            # resolves the practitioner's OWN display name and her OWN zone per
            # row and drops any row it cannot resolve, so no booking can fall
            # through to somebody else's name or somebody else's clock.
            #
            # start_ts>=? stays a Hawaii-naive comparison and is deliberately
            # left coarse. It is a "has this already happened" cut, and the
            # widest a practitioner's clock can sit from Hawaii's is under a
            # day, so at worst an appointment lingers or disappears within a
            # few hours of its true end. A row shown a little late is a far
            # smaller harm than one hidden early, and nothing here is stamped
            # or one-way: the next page load re-decides.
            cur = cx.execute(
                "SELECT id, session_type, practitioner, medium, start_ts, end_ts, prepaid "
                "FROM evox_bookings WHERE lower(email)=? AND status='booked' AND start_ts>=? "
                "ORDER BY start_ts ASC LIMIT 50", ((email or "").strip().lower(), now_iso))
            labels = {"biofield-consult": "Biofield Analysis Consultation",
                      "evox": "EVOX Session", "onboarding": "Welcome Call",
                      "triage": "Discovery Call"}
            # One entry per practitioner, not per booking. See
            # _practitioner_identity: resolving a name reaches Supabase and
            # this runs on a client's portal page load.
            identities = {}
            for row in cur.fetchall():
                item = _row_dict(cur, row)
                identity = _practitioner_identity(
                    cx, item.get("practitioner"), identities)
                if identity is None:
                    # Omitted on purpose, and never rendered under a default
                    # name or a default zone. A client seeing the wrong
                    # practitioner or the wrong hour is worse than a client
                    # seeing nothing, which is why this row is dropped.
                    continue
                who, tz_name, own_labels = identity
                kind = item.get("session_type") or "appointment"
                events.append({"id": f"appointment-{item['id']}", "type": "appointment",
                    "title": (own_labels.get(kind)
                              or labels.get(kind, "Private Appointment")),
                    "description": f"Private {item.get('medium') or 'session'} appointment with {who}.",
                    "start": _zoned_iso(item.get("start_ts"), tz_name),
                    "end": _zoned_iso(item.get("end_ts"), tz_name),
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
