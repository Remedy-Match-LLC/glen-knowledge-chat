"""A member who has registered for Group Coaching must see their join link.

Group Coaching registers ONCE PER RECURRING SERIES (live_event_series, Zoom
registration_type=1), but build_block only ever looked in
portal_event_registrations, which is keyed per occurrence. So a member who had
registered was still shown "Reserve my spot", with no link and no sign they were
already in.

Observed live 2026-09-02: Sam Terry was the single approved Zoom registrant for
Group Coaching and his card still read "Reserve my spot", while the MasterClass
card beside it rendered "Join session" with his zoom.us link. That asymmetry sat
behind 19 MasterClass registrations against 1 for Group Coaching.
"""
import sqlite3

import pytest

from dashboard import portal_calendar as pc
from dashboard import live_event_series as les

JOIN = "https://zoom.us/w/93500325091?tk=SERIESTOKEN"


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    cx.execute("""CREATE TABLE calendar_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, summary TEXT, start TEXT, "end" TEXT,
        location TEXT, status TEXT, calendar_name TEXT, zoom_meeting_id TEXT,
        zoom_registration_required INTEGER)""")
    cx.execute("INSERT INTO calendar_events(summary,start,\"end\",location,status,"
               "calendar_name,zoom_meeting_id,zoom_registration_required) VALUES"
               "('Group Coaching','2099-01-01T14:00:00','2099-01-01T15:00:00','',"
               "'visible','Group Coaching','93500325091',1)")
    cx.commit()
    return cx


def _gc(cx, email="member@x.com"):
    block = pc.build_block(cx, email=email, group_coaching_entitled=True,
                           now_iso="2026-01-01T00:00:00")
    return next(e for e in block["events"] if e["type"] == "group_coaching")


def test_a_series_registration_surfaces_as_a_join_link(tmp_path):
    cx = _db(tmp_path)
    les.set_registration(cx, "group-coaching", "member@x.com", "REG1", JOIN)
    ev = _gc(cx)
    assert ev["action_label"] == "Join session"
    assert ev["action_url"] == JOIN
    assert ev["registered"] is True, "the card must not offer to reserve again"


def test_without_any_registration_the_member_is_asked_to_reserve(tmp_path):
    cx = _db(tmp_path)
    ev = _gc(cx)
    assert ev["registered"] is False
    assert ev["action_url"] != JOIN


def test_a_per_occurrence_registration_still_wins(tmp_path):
    """The existing path must keep working; the series is a FALLBACK, not a
    replacement. An occurrence-specific link is the more precise one."""
    cx = _db(tmp_path)
    occ = "https://zoom.us/w/93500325091?tk=OCCURRENCE"
    pc.register_group(cx, "group-1", "member@x.com", join_url=occ)
    les.set_registration(cx, "group-coaching", "member@x.com", "REG1", JOIN)
    ev = _gc(cx)
    assert ev["action_url"] == occ
    assert ev["action_label"] == "Join session"


def test_another_members_series_registration_is_never_shown(tmp_path):
    """The link is personal. Keying it off the wrong email would hand one member
    another member's private Zoom join URL."""
    cx = _db(tmp_path)
    les.set_registration(cx, "group-coaching", "someone.else@x.com", "REG9", JOIN)
    ev = _gc(cx, email="member@x.com")
    assert ev["action_url"] != JOIN
    assert ev["registered"] is False


def test_a_locked_member_never_gets_a_link(tmp_path):
    """Entitlement is checked before any link is handed over."""
    cx = _db(tmp_path)
    les.set_registration(cx, "group-coaching", "member@x.com", "REG1", JOIN)
    block = pc.build_block(cx, email="member@x.com", group_coaching_entitled=False,
                           now_iso="2026-01-01T00:00:00")
    ev = next(e for e in block["events"] if e["type"] == "group_coaching")
    assert ev["locked"] is True
    assert ev["action_label"] == "Upgrade to access"
    assert JOIN not in ev["action_url"]
    # Also not flagged registered: the front-end branches on that, so a locked
    # member marked "registered" would render neither Upgrade nor Reserve.
    assert ev["registered"] is False
