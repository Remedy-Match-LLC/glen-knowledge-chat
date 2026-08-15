import sqlite3
from pathlib import Path

from dashboard import appointment_proposals as ap


def cx():
    return sqlite3.connect(":memory:")


def test_client_proposal_waits_for_staff_then_confirms():
    db = cx()
    pid = ap.create(db, email="C@X.com", session_type="evox",
                    start="2099-01-01T10:00:00", proposed_by="client",
                    billing_mode="prepaid")
    proposal = ap.get(db, pid)
    assert proposal["client_confirmed"] == 1
    assert proposal["staff_confirmed"] == 0
    assert proposal["status"] == "proposed"
    assert ap.confirm(db, pid, "staff")["status"] == "confirmed"


def test_staff_proposal_waits_for_client_and_thread_is_private_to_proposal():
    db = cx()
    pid = ap.create(db, email="c@x.com", session_type="biofield-consult",
                    start="2099-01-02T11:00:00", proposed_by="glen",
                    price_cents=15000)
    ap.add_message(db, pid, "glen", "Does this time work?", "note.mp3")
    proposal = ap.list_for_client(db, "C@X.COM")[0]
    assert proposal["staff_confirmed"] == 1
    assert proposal["client_confirmed"] == 0
    assert proposal["messages"][0]["text"] == "Does this time work?"
    assert proposal["messages"][0]["audio_filename"] == "note.mp3"


def test_wrong_session_type_rejected():
    db = cx()
    try:
        ap.create(db, email="c@x.com", session_type="other", start="x",
                  proposed_by="client")
    except ValueError as exc:
        assert str(exc) == "bad_session_type"
    else:
        raise AssertionError("expected bad session type")


def test_console_offers_confirm_and_different_time_actions():
    html = (Path(__file__).parents[1] / "static" / "appointment-proposals.html").read_text()
    assert "Confirm this time" in html
    assert "Propose a different time" in html
    assert "f.proposed_start.focus()" in html
