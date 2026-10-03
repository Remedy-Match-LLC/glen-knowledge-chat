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
    # No row factory: the app's connections return plain tuples, and a dict-only
    # read once failed silently there while these tests passed.
    cx = sqlite3.connect(":memory:")
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
    biofield_store.set_scan_confirmed(cx, E, True)   # stamped now, in UTC, by the store
    assert bp.status(cx, E, today=bp.business_today())["ready"] is True


def test_a_self_confirmed_scan_expires_with_the_window():
    """Round 2 review, 2026-10-02: the flag had no date, so one click counted forever."""
    cx = _cx()
    biofield_store.set_scan_confirmed(cx, E, True)
    cx.execute("UPDATE biofield_readiness SET scan_confirmed_at=? WHERE email=?",
               ("2026-09-24T12:00:00Z", E))
    assert bp.has_fresh_scan(cx, E, today=TODAY) is False
    cx.execute("UPDATE biofield_readiness SET scan_confirmed_at=? WHERE email=?",
               ("2026-09-26T12:00:00Z", E))
    assert bp.has_fresh_scan(cx, E, today=TODAY) is True


def test_an_undated_old_self_confirmation_is_not_trusted():
    cx = _cx()
    biofield_store.set_scan_confirmed(cx, E, True)
    cx.execute("UPDATE biofield_readiness SET scan_confirmed_at=NULL WHERE email=?", (E,))
    assert bp.has_fresh_scan(cx, E, today=TODAY) is False


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


def test_photo_is_required_for_ready():
    """Round 3 review: dropping photo from `ready` kept every test green."""
    cx = _cx()
    intake.submit(cx, E, {}, "2026-10-01T00:00:00Z")
    client_scans.upsert_scans(cx, E, [{"scan_date": "2026-10-01", "scan_id": "s1"}])
    st = bp.status(cx, E, today=TODAY)
    assert st["intake"] and st["scan"] and not st["photo"]
    assert st["ready"] is False


def test_a_confirmation_one_day_ahead_counts_two_days_does_not():
    """A UTC stamp runs a calendar day ahead of Hawai'i from 14:00 HST."""
    cx = _cx()
    biofield_store.set_scan_confirmed(cx, E, True)
    cx.execute("UPDATE biofield_readiness SET scan_confirmed_at=? WHERE email=?",
               ("2026-10-03T02:00:00Z", E))
    assert bp.has_fresh_scan(cx, E, today=TODAY) is True
    cx.execute("UPDATE biofield_readiness SET scan_confirmed_at=? WHERE email=?",
               ("2026-10-04T02:00:00Z", E))
    assert bp.has_fresh_scan(cx, E, today=TODAY) is False


def test_a_failed_read_does_not_poison_the_reads_after_it():
    """A missing table must cost only its own source. On Postgres an unguarded
    failure aborts the transaction, so every later read would fail too."""
    cx = _cx()
    cx.execute("DROP TABLE biofield_readiness")
    client_photos.put(cx, E, b"jpeg", "image/jpeg")
    intake.submit(cx, E, {}, "2026-10-01T00:00:00Z")
    client_scans.upsert_scans(cx, E, [{"scan_date": "2026-10-01", "scan_id": "s1"}])
    assert bp.status(cx, E, today=TODAY)["ready"] is True
    assert cx.execute("SELECT 1").fetchone()[0] == 1


def test_a_table_without_the_new_column_still_counts_photo_and_intake():
    cx = _cx()
    cx.execute("DROP TABLE biofield_readiness")
    cx.execute("CREATE TABLE biofield_readiness (email TEXT PRIMARY KEY, paid_at TEXT, "
               "photo_on_file INTEGER, intake_confirmed INTEGER, scan_confirmed INTEGER)")
    cx.execute("INSERT INTO biofield_readiness VALUES (?, NULL, 1, 1, 1)", (E,))
    st = bp.status(cx, E, today=TODAY)
    assert st["photo"] and st["intake"]
    assert st["scan"] is False, "an undated confirmation is not a fresh scan"
