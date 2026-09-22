"""The two Wednesday live classes swap times, effective Wednesday 1 October 2026.

Glen, 2026-09-20: the free Wellness Whispering MasterClass moves to 2:00 PM HST and Group
Coaching moves to 3:00 PM HST, both weekly on Wednesday. He chose this so the PAID session
has no hard stop behind it. The recordings showed why: Group Coaching ran 50 minutes, 1 hour
8 and 58 minutes on 2, 9 and 16 September, squeezed by the free class after it.

Glen, 2026-09-21: first Wednesday at the new times is 1 October, not 24 September, so members
get a full week's notice in the 29 September invitation.

Three things had to move together, and each test below pins one of them:
  - the publish step's hours (Monday publishes the next two Wednesdays),
  - the Monday invitation email's two hardcoded lines,
  - events ALREADY published at the old times, which must be MOVED IN PLACE, because a Group
    Coaching RSVP is keyed "group-<calendar_events.id>". Replacing the row would orphan it.

Zoom is the fourth part and is not code: both recurring meetings must be moved on Glen's Zoom
account at the same time as this deploys, because the publish step asks Zoom for each
Wednesday's occurrence AT THE EXACT TIME it expects, and fails the whole publish on a miss.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod

HST = ZoneInfo("Pacific/Honolulu")


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=HST)


# --- the hours -------------------------------------------------------------------------

def test_the_masterclass_is_first_and_group_coaching_second():
    master, group = appmod._wednesday_live_starts(_at(2026, 9, 28, 8))   # a Monday
    assert [(s.hour, s.minute) for s in master] == [(14, 0), (14, 0)]
    assert [(s.hour, s.minute) for s in group] == [(15, 0), (15, 0)]


def test_both_classes_fall_on_the_same_two_wednesdays():
    master, group = appmod._wednesday_live_starts(_at(2026, 9, 28, 8))   # Monday 28 Sep
    assert [s.date().isoformat() for s in master] == ["2026-09-30", "2026-10-07"]
    assert [s.date() for s in group] == [s.date() for s in master]
    assert all(s.weekday() == 2 for s in master + group), "both must be Wednesdays"


def test_a_wednesday_whose_first_class_has_started_rolls_both_to_next_week():
    """The old code decided 'this Wednesday or next' from Group Coaching's start. With the
    MasterClass now first, a run at 2:30 on a Wednesday would have kept Group Coaching (3:00)
    on this Wednesday while the 2:00 class was already under way. Both must move together."""
    master, group = appmod._wednesday_live_starts(_at(2026, 10, 7, 14, 30))
    assert master[0].date().isoformat() == "2026-10-14"
    assert group[0].date() == master[0].date()


def test_a_wednesday_morning_run_still_publishes_that_wednesday():
    master, group = appmod._wednesday_live_starts(_at(2026, 10, 7, 9))
    assert master[0].date().isoformat() == "2026-10-07"
    assert group[0].date().isoformat() == "2026-10-07"


# --- the Monday invitation -------------------------------------------------------------

def _invitation_text():
    from scripts import weekly_live_invitation as wli
    text, _html = wli._copy("Friend", "https://example.test/portal", True,
                            datetime(2026, 10, 1).date(), "")
    return text


def test_the_invitation_lists_the_masterclass_at_two_and_coaching_at_three():
    text = _invitation_text()
    assert "2:00 PM HST: Free Wellness Whispering MasterClass" in text
    assert "3:00 PM HST: Group Coaching" in text
    assert text.index("2:00 PM HST") < text.index("3:00 PM HST"), "earlier class first"


def test_the_invitation_no_longer_carries_the_old_pairings():
    text = _invitation_text()
    assert "2:00 PM HST: Group Coaching" not in text
    assert "3:00 PM HST: Free Wellness Whispering MasterClass" not in text


# --- events already published at the old times -----------------------------------------
# On 2026-09-22 the OLD code publishes 24 Sep and 1 Oct at the old hours, and members RSVP
# against those rows. The swap's first run must MOVE the 1 Oct rows, never duplicate them.

import sqlite3
from dashboard import db as _db


def _occ(dt):
    return "occ-" + dt.replace(second=0, microsecond=0).isoformat()


class _FakeZoom:
    """Zoom AFTER Glen's two recurring meetings were moved: each meeting's occurrences sit
    at the NEW hours. Records every meeting it was asked for."""

    def __init__(self, master_starts, group_starts):
        self.by_id = {
            "11111111111": {"meeting_id": "11111111111", "type": 8,
                            "settings": {"approval_type": 0, "registration_type": 1},
                            "registration_url": "https://zoom.test/reg/gc",
                            "occurrences": [{"occurrence_id": _occ(s),
                                             "start_time": s.isoformat()} for s in group_starts]},
            "22222222222": {"meeting_id": "22222222222", "type": 8,
                            "settings": {"approval_type": 0, "registration_type": 1},
                            "registration_url": "https://zoom.test/reg/mc",
                            "occurrences": [{"occurrence_id": _occ(s),
                                             "start_time": s.isoformat()} for s in master_starts]},
        }

    def get_meeting(self, token, meeting_id):
        return dict(self.by_id[str(meeting_id)])


@pytest.fixture
def published_at_old_times(monkeypatch, tmp_path):
    from dashboard import masterclass as _mc, live_event_series as _les, portal_calendar as _pc
    from dashboard import zoom as _zoom
    path = str(tmp_path / "live.db")
    monkeypatch.setattr(appmod, "LOG_DB", path)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    for k in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.setenv(k, "fake")

    master_starts, group_starts = appmod._wednesday_live_starts(datetime.now(HST))
    fake = _FakeZoom(master_starts, group_starts)
    monkeypatch.setattr(_zoom, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(_zoom, "get_meeting", fake.get_meeting)

    appmod._init_calendar_table()
    wed = group_starts[0]
    old_group = wed.replace(hour=14, tzinfo=None)          # the OLD Group Coaching hour
    old_master = wed.replace(hour=15, tzinfo=None)         # the OLD MasterClass hour
    with _db.connect(path) as cx:
        _mc.init_masterclass_tables(cx)
        _les.init_tables(cx)
        _pc.init_registration_table(cx)
        _les.upsert_series(cx, "group-coaching", "Group Coaching", "11111111111", "")
        _les.upsert_series(cx, "free-masterclass", "Free Wellness Whispering MasterClass",
                           "22222222222", "")
        cx.execute(
            "INSERT INTO calendar_events (id,pushed_at,google_cal_id,google_event_id,"
            'calendar_name,summary,start,"end",location,owner,status,cal_alert,'
            "zoom_meeting_id,zoom_occurrence_id,zoom_registration_url,"
            "zoom_registration_required) VALUES (77,'x','community','old-gc','Group Coaching',"
            "'Group Coaching',?,?,'Zoom','glen','visible',0,'11111111111','occ-OLD','',1)",
            (old_group.isoformat(), (old_group + timedelta(hours=1)).isoformat()))
        mc_id = _mc.create_event(cx, topic="Free Wellness Whispering MasterClass",
                                 description="x", start_ts=old_master.isoformat(),
                                 duration_min=60, price_cents=0, member_price_cents=0)
        # A member who reserved the 1 October Group Coaching against the OLD row.
        _pc.register_group(cx, "group-77", "member@x.com", meeting_id="11111111111",
                           occurrence_id="occ-OLD", join_url="https://zoom.test/j/member")
        cx.commit()
    return path, wed, mc_id, master_starts, group_starts


def _bootstrap():
    r = appmod.app.test_client().post("/api/console/community-live/bootstrap",
                                      headers={"X-Console-Key": "secret"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_an_old_time_group_coaching_row_is_moved_not_duplicated(published_at_old_times):
    path, wed, _mc_id, _m, group_starts = published_at_old_times
    _bootstrap()
    with sqlite3.connect(path) as cx:
        rows = cx.execute(
            "SELECT id,start,zoom_occurrence_id FROM calendar_events WHERE status='visible' "
            "AND lower(summary) LIKE '%group coaching%' AND start LIKE ?",
            (wed.date().isoformat() + "%",)).fetchall()
    assert len(rows) == 1, f"the class appears {len(rows)} times on {wed.date()}: {rows}"
    row_id, start, occurrence = rows[0]
    assert row_id == 77, "the SAME row must carry the new time, or its RSVPs are orphaned"
    assert start.startswith(wed.date().isoformat() + "T15:00")
    assert occurrence == _occ(group_starts[0]), "must point at Zoom's NEW occurrence"


def test_an_old_time_masterclass_row_is_moved_not_duplicated(published_at_old_times):
    path, wed, mc_id, master_starts, _g = published_at_old_times
    _bootstrap()
    with sqlite3.connect(path) as cx:
        rows = cx.execute(
            "SELECT id,start_ts FROM masterclass_events "
            "WHERE lower(topic) LIKE '%wellness whispering%' AND start_ts LIKE ?",
            (wed.date().isoformat() + "%",)).fetchall()
    assert len(rows) == 1, rows
    assert rows[0][0] == mc_id
    assert rows[0][1].startswith(wed.date().isoformat() + "T14:00")


def test_a_members_rsvp_survives_the_move(published_at_old_times):
    """The reason for moving in place. The RSVP is keyed "group-77" and must still name a
    visible Group Coaching row after the swap."""
    path, wed, _mc_id, _m, _g = published_at_old_times
    _bootstrap()
    with sqlite3.connect(path) as cx:
        reg = cx.execute("SELECT event_key FROM portal_event_registrations "
                         "WHERE lower(email)='member@x.com'").fetchone()
        assert reg and reg[0] == "group-77"
        target = cx.execute("SELECT start FROM calendar_events WHERE id=77 "
                            "AND status='visible'").fetchone()
    assert target, "the RSVP now points at a row that is gone or hidden"
    assert target[0].startswith(wed.date().isoformat() + "T15:00")


def test_the_publish_fails_loudly_if_zoom_was_not_moved(published_at_old_times, monkeypatch):
    """The coupling this change depends on. If the code ships and the two Zoom meetings are
    still at the old hours, Zoom has no occurrence at the new time and the publish must FAIL,
    never quietly publish a class at a time Zoom does not have."""
    from dashboard import zoom as _zoom
    path, wed, _mc_id, master_starts, group_starts = published_at_old_times
    stale = _FakeZoom([s.replace(hour=15) for s in master_starts],
                      [s.replace(hour=14) for s in group_starts])   # Zoom still at OLD hours
    monkeypatch.setattr(_zoom, "get_meeting", stale.get_meeting)
    r = appmod.app.test_client().post("/api/console/community-live/bootstrap",
                                      headers={"X-Console-Key": "secret"})
    assert r.status_code == 502
    with sqlite3.connect(path) as cx:
        start = cx.execute("SELECT start FROM calendar_events WHERE id=77").fetchone()[0]
    assert start.startswith(wed.date().isoformat() + "T14:00"), "a failed publish moved nothing"
