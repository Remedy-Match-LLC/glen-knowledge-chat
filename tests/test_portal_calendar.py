import sqlite3

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
