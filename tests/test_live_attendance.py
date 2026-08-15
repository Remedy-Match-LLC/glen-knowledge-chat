import sqlite3

import pytest

from dashboard import live_attendance


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    live_attendance.init_tables(cx)
    return cx


def test_attendee_record_is_email_keyed_and_idempotent():
    cx = _cx()
    values = dict(event_key="masterclass-7", event_type="free-masterclass",
                  event_date_hst="2026-08-19", meeting_id="123",
                  meeting_uuid="uuid-1", email="Person@Example.com",
                  display_name="Person", first_join="2026-08-20T00:01:00Z",
                  last_leave="2026-08-20T01:00:00Z", duration_minutes=59,
                  raw_join_count=2, matched_ghl=True,
                  tagged_at="2026-08-20T12:00:00Z")
    live_attendance.record_attendee(cx, **values)
    values.update(duration_minutes=61, raw_join_count=3)
    live_attendance.record_attendee(cx, **values)
    rows = cx.execute("SELECT * FROM live_event_attendance").fetchall()
    assert len(rows) == 1
    assert rows[0]["email"] == "person@example.com"
    assert rows[0]["duration_minutes"] == 61
    assert rows[0]["raw_join_count"] == 3


def test_attendee_without_verified_email_is_rejected():
    with pytest.raises(ValueError, match="verified attendee email"):
        live_attendance.record_attendee(
            _cx(), event_key="group-1", event_type="group-coaching",
            event_date_hst="2026-08-19", meeting_id="123", meeting_uuid="uuid-1",
            email="", display_name="iPad")


def test_report_evidence_upserts_instead_of_duplicating():
    cx = _cx()
    payload = dict(event_key="group-1", event_type="group-coaching",
                   event_date_hst="2026-08-19", meeting_id="123",
                   meeting_uuid="uuid-1", raw_participant_count=8,
                   human_attendee_count=5, matched_tagged_count=4,
                   unmatched_count=1, excluded_host_bot_count=3,
                   status="needs_attention")
    live_attendance.record_report_run(cx, **payload)
    payload.update(matched_tagged_count=5, unmatched_count=0, status="verified_tagged")
    live_attendance.record_report_run(cx, **payload)
    rows = cx.execute("SELECT * FROM live_event_report_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["matched_tagged_count"] == 5
    assert rows[0]["status"] == "verified_tagged"
