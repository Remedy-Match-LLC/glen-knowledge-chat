import sqlite3

from dashboard import portal_calendar


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE masterclass_events (id INTEGER, topic TEXT, description TEXT, start_ts TEXT, duration_min INTEGER)")
    cx.execute('CREATE TABLE calendar_events (id INTEGER, calendar_name TEXT, summary TEXT, start TEXT, "end" TEXT, location TEXT, status TEXT)')
    cx.execute("INSERT INTO masterclass_events VALUES (1,'Healing Q&A','Ask Dr. Glen','2099-02-01T10:00:00',60)")
    cx.execute("INSERT INTO calendar_events VALUES (2,'Group Coaching','Live Group Coaching','2099-02-02T10:00:00','2099-02-02T11:00:00','https://zoom.test/private','visible')")
    cx.execute("CREATE TABLE evox_bookings (id INTEGER, email TEXT, session_type TEXT, practitioner TEXT, medium TEXT, start_ts TEXT, end_ts TEXT, prepaid INTEGER, status TEXT)")
    return cx


def test_free_member_sees_events_but_not_private_join_url():
    block = portal_calendar.build_block(_cx(), group_coaching_entitled=False,
                                        now_iso="2099-01-01T00:00:00")
    assert [e["type"] for e in block["events"]] == ["masterclass", "group_coaching"]
    coaching = block["events"][1]
    assert coaching["locked"] is True
    assert coaching["action_url"] == "/membership"
    assert "zoom.test" not in str(block)


def test_entitled_member_receives_group_join_url():
    block = portal_calendar.build_block(_cx(), group_coaching_entitled=True,
                                        now_iso="2099-01-01T00:00:00")
    coaching = block["events"][1]
    assert coaching["locked"] is False
    assert coaching["action_url"] == "https://zoom.test/private"


def test_group_registration_is_remembered():
    cx = _cx()
    portal_calendar.register_group(cx, "group-2", "Member@X.com")
    block = portal_calendar.build_block(cx, email="member@x.com",
        group_coaching_entitled=True, now_iso="2099-01-01T00:00:00")
    assert block["events"][1]["registered"] is True


def test_naive_hawaii_time_is_sent_with_offset_for_browser_conversion():
    block = portal_calendar.build_block(_cx(), now_iso="2099-01-01T00:00:00")
    assert block["events"][0]["start"].endswith("-10:00")


def test_confirmed_private_appointment_appears_only_for_its_client():
    db = _cx()
    db.execute("INSERT INTO evox_bookings VALUES (7,'client@x.com','evox','rae','phone','2099-02-03T10:00:00','2099-02-03T11:00:00',1,'booked')")
    own = portal_calendar.build_block(db, email="client@x.com", now_iso="2099-01-01T00:00:00")
    other = portal_calendar.build_block(db, email="other@x.com", now_iso="2099-01-01T00:00:00")
    assert any(e["id"] == "appointment-7" for e in own["events"])
    assert not any(e["id"] == "appointment-7" for e in other["events"])
