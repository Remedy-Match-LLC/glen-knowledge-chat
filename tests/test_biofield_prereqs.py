"""Glen 2026-10-02: a Biofield client finishes the scan, intake and photo before paying.

Each step must be satisfied by the records the portal writes as well as the ones the
readiness page writes, so the two pages can never disagree.
"""
import sqlite3

from dashboard import (biofield_prereqs as bp, biofield_store, client_photos, client_scans,
                       intake, scan_freshness)

TODAY = "2026-10-02"
E = "c@x.com"


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    biofield_store.init_table(cx)
    client_photos.init_table(cx)
    client_scans.init_client_scans_table(cx)
    intake.init_intake_table(cx)
    scan_freshness.init_table(cx)
    cx.execute("CREATE TABLE inbound_leads (email TEXT, source TEXT)")
    return cx


def test_nothing_done_is_not_ready():
    st = bp.status(_cx(), E, today=TODAY)
    assert st == {"photo": False, "intake": False, "scan": False, "ready": False}


def test_portal_records_alone_make_a_client_ready():
    cx = _cx()
    client_photos.put(cx, E, b"jpeg", "image/jpeg")
    intake.submit(cx, E, {}, "2026-10-01T00:00:00Z")
    client_scans.upsert_scans(cx, E, [{"scan_date": "2026-09-30", "scan_id": "s1"}])
    assert bp.status(cx, E, today=TODAY)["ready"] is True


def test_readiness_page_records_alone_make_a_client_ready():
    cx = _cx()
    biofield_store.set_photo_on_file(cx, E, "x.jpg")
    biofield_store.set_intake_confirmed(cx, E, True)
    biofield_store.set_scan_confirmed(cx, E, True)
    assert bp.status(cx, E, today=TODAY)["ready"] is True


def test_intake_lead_from_a_known_source_counts():
    cx = _cx()
    cx.execute("INSERT INTO inbound_leads VALUES (?, ?)", (E.upper(), "scoreapp"))
    assert bp.has_intake(cx, E) is True
    cx2 = _cx()
    cx2.execute("INSERT INTO inbound_leads VALUES (?, ?)", (E, "newsletter"))
    assert bp.has_intake(cx2, E) is False


def test_a_draft_intake_does_not_count():
    cx = _cx()
    intake.save_draft(cx, E, {}, "2026-10-01T00:00:00Z")
    assert bp.has_intake(cx, E) is False


def test_an_old_scan_is_not_fresh():
    cx = _cx()
    client_scans.upsert_scans(cx, E, [{"scan_date": "2026-09-24", "scan_id": "old"}])
    scan_freshness.upsert(cx, [{"email": E, "last_scan_date": "2026-09-24"}])
    assert bp.has_fresh_scan(cx, E, today=TODAY) is False


def test_a_scan_seven_days_old_is_still_fresh():
    cx = _cx()
    client_scans.upsert_scans(cx, E, [{"scan_date": "2026-09-25", "scan_id": "edge"}])
    assert bp.has_fresh_scan(cx, E, today=TODAY) is True


def test_the_freshness_index_alone_counts():
    cx = _cx()
    scan_freshness.upsert(cx, [{"email": E, "last_scan_date": "2026-10-01"}])
    assert bp.has_fresh_scan(cx, E, today=TODAY) is True


def test_missing_tables_read_as_not_done_and_never_raise():
    cx = sqlite3.connect(":memory:")
    assert bp.status(cx, E, today=TODAY)["ready"] is False


def test_blank_email_is_never_ready():
    cx = _cx()
    biofield_store.set_photo_on_file(cx, "", "x.jpg")
    assert bp.status(cx, "", today=TODAY)["ready"] is False
