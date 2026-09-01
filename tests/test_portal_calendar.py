import contextlib
import sqlite3
from datetime import datetime

from dashboard import portal_calendar


def test_row_dict_accepts_postgres_mapping_without_cursor_description():
    class PgCursor:
        pass
    assert portal_calendar._row_dict(PgCursor(), {"id": 7, "topic": "T"}) == {
        "id": 7, "topic": "T"}


def test_missing_optional_table_does_not_poison_postgres_transaction():
    class AbortingConnection:
        def __init__(self):
            self.inner = _cx()
            self.aborted = False

        def execute(self, sql, params=()):
            if self.aborted:
                raise RuntimeError("current transaction is aborted")
            try:
                return self.inner.execute(sql, params)
            except Exception:
                self.aborted = True
                raise

        def commit(self):
            self.inner.commit()

        def rollback(self):
            self.inner.rollback()
            self.aborted = False

    block = portal_calendar.build_block(
        AbortingConnection(), email="steve@example.com",
        now_iso="2099-01-01T00:00:00")
    assert any(event["type"] == "masterclass" for event in block["events"])


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE masterclass_events (id INTEGER, topic TEXT, description TEXT, "
               "start_ts TEXT, duration_min INTEGER, price_cents INTEGER, member_price_cents INTEGER)")
    cx.execute('CREATE TABLE calendar_events (id INTEGER, calendar_name TEXT, summary TEXT, start TEXT, "end" TEXT, location TEXT, status TEXT)')
    cx.execute("INSERT INTO masterclass_events VALUES "
               "(1,'Healing Q&A','Ask Dr. Glen','2099-02-01T10:00:00',60,0,0)")
    cx.execute("INSERT INTO calendar_events VALUES (2,'Group Coaching','Live Group Coaching','2099-02-02T10:00:00','2099-02-02T11:00:00','https://zoom.test/private','visible')")
    cx.execute("CREATE TABLE evox_bookings (id INTEGER, email TEXT, session_type TEXT, practitioner TEXT, medium TEXT, start_ts TEXT, end_ts TEXT, prepaid INTEGER, status TEXT)")
    cx.execute("CREATE TABLE affiliate_signups (email TEXT, slug TEXT, status TEXT)")
    return cx


def test_default_clock_uses_hawaii_wall_time(monkeypatch):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz):
            assert str(tz) == "Pacific/Honolulu"
            return datetime(2026, 8, 26, 11, 30, tzinfo=tz)

    monkeypatch.setattr(portal_calendar, "datetime", FrozenDateTime)
    assert portal_calendar._now_iso() == "2026-08-26T11:30:00"


def test_free_member_sees_events_but_not_private_join_url():
    block = portal_calendar.build_block(_cx(), group_coaching_entitled=False,
                                        now_iso="2099-01-01T00:00:00")
    assert [e["type"] for e in block["events"][:2]] == ["masterclass", "group_coaching"]
    coaching = block["events"][1]
    assert coaching["locked"] is True
    assert coaching["action_url"] == "/membership"
    assert "zoom.test" not in str(block)


def test_entitled_member_does_not_receive_shared_group_join_url_before_registration():
    block = portal_calendar.build_block(_cx(), group_coaching_entitled=True,
                                        now_iso="2099-01-01T00:00:00")
    coaching = block["events"][1]
    assert coaching["locked"] is False
    assert coaching["action_url"] == ""
    assert coaching["registered"] is False


def test_group_registration_is_remembered():
    cx = _cx()
    portal_calendar.register_group(cx, "group-2", "Member@X.com",
        meeting_id="123", registrant_id="reg-1",
        join_url="https://zoom.test/private-member")
    block = portal_calendar.build_block(cx, email="member@x.com",
        group_coaching_entitled=True, now_iso="2099-01-01T00:00:00")
    assert block["events"][1]["registered"] is True
    assert block["events"][1]["action_url"] == "https://zoom.test/private-member"


def test_group_session_remains_visible_until_its_end_time():
    block = portal_calendar.build_block(
        _cx(), group_coaching_entitled=True,
        now_iso="2099-02-02T10:30:00")
    assert any(e["type"] == "group_coaching" for e in block["events"])


def test_group_session_disappears_after_its_end_time():
    block = portal_calendar.build_block(
        _cx(), group_coaching_entitled=True,
        now_iso="2099-02-02T11:00:01")
    assert not any(e["type"] == "group_coaching" for e in block["events"])


def test_approved_ambassador_gets_share_link_for_free_masterclass_only():
    cx = _cx()
    cx.execute("INSERT INTO affiliate_signups VALUES "
               "('ambassador@example.com','ambassador-one','approved')")
    cx.execute("INSERT INTO masterclass_events VALUES "
               "(9,'Paid Intensive','Paid class','2099-02-03T10:00:00',60,5000,0)")
    block = portal_calendar.build_block(
        cx, email="ambassador@example.com", now_iso="2099-01-01T00:00:00")
    masterclass = next(e for e in block["events"] if e["type"] == "masterclass")
    assert masterclass["share_url"] == "/masterclass/1?ref=ambassador-one"
    paid = next(e for e in block["events"] if e["id"] == "masterclass-9")
    assert paid["share_url"] == ""


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


def _booking_config(cx, pid, tz, *, slug="intro", label="Free 20 minute intro call",
                    medium="phone"):
    """Give `pid` a real booking config, written through the module that owns it."""
    from dashboard import practitioner_booking as pb
    pb.init_tables(cx)
    pb.set_config(cx, pid, {
        "timezone": tz, "office_hours": "1-5:09:00-17:00",
        "session_types": [{"slug": slug, "label": label,
                           "duration_min": 20, "medium": medium}],
        "notice_hours": 24, "buffer_min": 0, "enabled": True})


def _fake_supabase(monkeypatch, names, calls=None):
    """Stand in for the practitioners table, recording every id looked up."""
    import db_supabase

    class _Cur:
        def __init__(self):
            self.row = None

        def execute(self, sql, params):
            pid = str(params[0])
            if calls is not None:
                calls.append(pid)
            self.row = {"name": names[pid]} if pid in names else None

        def fetchone(self):
            return self.row

    @contextlib.contextmanager
    def _fake():
        yield _Cur()

    monkeypatch.setattr(db_supabase, "supabase_cursor", _fake)


def _pg_shaped(cx):
    """Rows read by column name, the way the portal route reads them."""
    cx.row_factory = sqlite3.Row
    return cx


def test_a_public_practitioners_booking_shows_her_own_name_and_her_own_zone(monkeypatch):
    """Was: excluded, because this view could only say "Rae" at a Honolulu time.

    The exclusion was right while the view hardcoded both. Now that the name
    and the zone are resolved per row, mary's client sees the appointment in
    their own portal, under mary's name, at mary's wall clock. 05:00 was
    written on an Anchorage clock, so it must carry Anchorage's offset, and
    the word "Rae" must appear nowhere near it.
    """
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-mary", "America/Anchorage")
    _fake_supabase(monkeypatch, {"pid-mary": "Dr. Mary Chen"})
    db.execute("INSERT INTO evox_bookings VALUES "
               "(8,'client@x.com','intro','pid-mary','zoom','2099-02-03T05:00:00',"
               "'2099-02-03T05:20:00',0,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        now_iso="2099-01-01T00:00:00")
    appointment = next(e for e in block["events"] if e["id"] == "appointment-8")
    assert "Dr. Mary Chen" in appointment["description"]
    assert appointment["start"] == "2099-02-03T05:00:00-09:00"
    assert appointment["end"] == "2099-02-03T05:20:00-09:00"
    assert "Rae" not in str(block), \
        "mary's client must never see her appointment labelled Rae"


def test_rae_and_glen_appointments_are_unchanged(monkeypatch):
    """Their zone IS Hawaii and their names are this view's own. Proved by
    behaviour: Supabase is made to explode and neither has a booking config,
    and both rows still render exactly as they did before."""
    import db_supabase

    def boom(*a, **kw):
        raise AssertionError("a legacy practitioner reached for Supabase")

    monkeypatch.setattr(db_supabase, "supabase_cursor", boom)
    db = _pg_shaped(_cx())
    db.execute("INSERT INTO evox_bookings VALUES (7,'client@x.com','evox','rae',"
               "'phone','2099-02-03T10:00:00','2099-02-03T11:00:00',1,'booked')")
    db.execute("INSERT INTO evox_bookings VALUES (9,'client@x.com','biofield-consult',"
               "'glen','zoom','2099-02-04T09:00:00','2099-02-04T10:00:00',1,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        now_iso="2099-01-01T00:00:00")
    rae = next(e for e in block["events"] if e["id"] == "appointment-7")
    glen = next(e for e in block["events"] if e["id"] == "appointment-9")
    assert rae["description"] == "Private phone appointment with Rae."
    assert rae["start"] == "2099-02-03T10:00:00-10:00"
    assert rae["end"] == "2099-02-03T11:00:00-10:00"
    assert rae["title"] == "EVOX Session"
    assert rae["action_label"] == "Confirmed"
    assert rae["prepaid"] is True
    assert glen["description"] == "Private zoom appointment with Dr. Glen."
    assert glen["start"] == "2099-02-04T09:00:00-10:00"
    assert glen["title"] == "Biofield Analysis Consultation"


def test_a_practitioner_with_no_resolvable_name_is_omitted(monkeypatch):
    """A zone but no name. Omitted, not shown under a default: the whole
    reason the old filter existed is that a wrong name is worse than a
    missing row."""
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-mary", "America/Anchorage")
    _booking_config(db, "pid-nina", "America/Anchorage")
    _fake_supabase(monkeypatch, {"pid-nina": "Dr. Nina Ross"})   # no row for mary
    db.execute("INSERT INTO evox_bookings VALUES "
               "(8,'client@x.com','intro','pid-mary','zoom','2099-02-03T05:00:00',"
               "'2099-02-03T05:20:00',0,'booked')")
    db.execute("INSERT INTO evox_bookings VALUES "
               "(9,'client@x.com','intro','pid-nina','zoom','2099-02-04T05:00:00',"
               "'2099-02-04T05:20:00',0,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        now_iso="2099-01-01T00:00:00")
    assert not any(e["id"] == "appointment-8" for e in block["events"])
    assert "Rae" not in str(block)
    # The omission is specific to the practitioner who could not be resolved,
    # not this whole section going dark.
    assert any(e["id"] == "appointment-9" for e in block["events"])


def test_a_practitioner_with_no_resolvable_timezone_is_omitted(monkeypatch):
    """A name but no readable booking config. There is no safe default zone:
    stamping Honolulu on an Anchorage appointment tells the client an hour
    that is not theirs, in a view that also says Confirmed."""
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-nina", "America/Anchorage")   # mary has no row
    _fake_supabase(monkeypatch, {"pid-mary": "Dr. Mary Chen",
                                 "pid-nina": "Dr. Nina Ross"})
    db.execute("INSERT INTO evox_bookings VALUES "
               "(8,'client@x.com','intro','pid-mary','zoom','2099-02-03T05:00:00',"
               "'2099-02-03T05:20:00',0,'booked')")
    db.execute("INSERT INTO evox_bookings VALUES "
               "(9,'client@x.com','intro','pid-nina','zoom','2099-02-04T05:00:00',"
               "'2099-02-04T05:20:00',0,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        now_iso="2099-01-01T00:00:00")
    assert not any(e["id"] == "appointment-8" for e in block["events"])
    assert "Dr. Mary Chen" not in str(block)
    assert any(e["id"] == "appointment-9" for e in block["events"])


def test_a_public_booking_is_visible_only_to_its_own_client(monkeypatch):
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-mary", "America/Anchorage")
    _fake_supabase(monkeypatch, {"pid-mary": "Dr. Mary Chen"})
    db.execute("INSERT INTO evox_bookings VALUES "
               "(8,'client@x.com','intro','pid-mary','zoom','2099-02-03T05:00:00',"
               "'2099-02-03T05:20:00',0,'booked')")
    own = portal_calendar.build_block(db, email="client@x.com",
                                      now_iso="2099-01-01T00:00:00")
    other = portal_calendar.build_block(db, email="other@x.com",
                                        now_iso="2099-01-01T00:00:00")
    assert any(e["id"] == "appointment-8" for e in own["events"])
    assert not any(e["id"] == "appointment-8" for e in other["events"])
    assert "Dr. Mary Chen" not in str(other)


def test_each_practitioner_is_resolved_once_per_page_load(monkeypatch):
    """This runs on a client's portal page load. A client with several
    bookings must not cost one Supabase round trip per booking."""
    calls = []
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-mary", "America/Anchorage")
    _fake_supabase(monkeypatch, {"pid-mary": "Dr. Mary Chen"}, calls=calls)
    for bid, day in ((8, "03"), (10, "04"), (11, "05")):
        db.execute("INSERT INTO evox_bookings VALUES "
                   f"({bid},'client@x.com','intro','pid-mary','zoom',"
                   f"'2099-02-{day}T05:00:00','2099-02-{day}T05:20:00',0,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        now_iso="2099-01-01T00:00:00")
    assert len([e for e in block["events"] if e["type"] == "appointment"]) == 3
    assert calls == ["pid-mary"], \
        f"one lookup per practitioner per page load, got {calls}"


def test_portal_authored_events_stay_hawaii_alongside_a_public_booking(monkeypatch):
    """Only evox_bookings rows carry another practitioner's zone. MasterClass
    and group coaching are authored in Hawai'i wall time and must not become
    zone-aware by accident."""
    db = _pg_shaped(_cx())
    _booking_config(db, "pid-mary", "America/Anchorage")
    _fake_supabase(monkeypatch, {"pid-mary": "Dr. Mary Chen"})
    db.execute("INSERT INTO evox_bookings VALUES "
               "(8,'client@x.com','intro','pid-mary','zoom','2099-02-03T05:00:00',"
               "'2099-02-03T05:20:00',0,'booked')")
    block = portal_calendar.build_block(db, email="client@x.com",
                                        group_coaching_entitled=True,
                                        now_iso="2099-01-01T00:00:00")
    appointment = next(e for e in block["events"] if e["id"] == "appointment-8")
    masterclass = next(e for e in block["events"] if e["type"] == "masterclass")
    coaching = next(e for e in block["events"] if e["type"] == "group_coaching")
    assert appointment["start"].endswith("-09:00")
    assert masterclass["start"].endswith("-10:00")
    assert coaching["start"].endswith("-10:00")
    assert coaching["end"].endswith("-10:00")

def test_old_shared_links_do_not_manufacture_future_occurrences():
    db = _cx()
    db.execute("DELETE FROM masterclass_events")
    db.execute("DELETE FROM calendar_events")
    db.execute("INSERT INTO masterclass_events "
               "(id,topic,description,start_ts,duration_min,price_cents,member_price_cents) VALUES "
               "(3,'Free Wellness Whispering MasterClass','Free live class',"
               "'2026-08-12T15:00:00',60,0,0)")
    db.execute("INSERT INTO calendar_events VALUES "
               "(4,'Group Coaching','Group Coaching','2026-08-12T13:00:00',"
               "'2026-08-12T14:00:00','https://zoom.test/weekly','visible')")
    block = portal_calendar.build_block(
        db, group_coaching_entitled=True, now_iso="2026-08-13T00:00:00")
    assert not [e for e in block["events"] if e["type"] == "masterclass"]
    assert not [e for e in block["events"] if e["type"] == "group_coaching"]


def test_only_concrete_future_occurrence_is_returned():
    db = _cx()
    db.execute("INSERT INTO masterclass_events "
               "(id,topic,description,start_ts,duration_min,price_cents,member_price_cents) VALUES "
               "(3,'Free Wellness Whispering MasterClass','Free live class',"
               "'2026-08-12T15:00:00',60,0,0)")
    db.execute("INSERT INTO masterclass_events "
               "(id,topic,description,start_ts,duration_min,price_cents,member_price_cents) VALUES "
               "(4,'Free Wellness Whispering MasterClass','Free live class',"
               "'2026-08-19T15:00:00',60,0,0)")
    block = portal_calendar.build_block(db, now_iso="2026-08-13T00:00:00")
    starts = [e["start"] for e in block["events"]
              if e["title"] == "Free Wellness Whispering MasterClass"]
    assert starts.count("2026-08-19T15:00:00-10:00") == 1
