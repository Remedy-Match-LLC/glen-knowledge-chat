"""Routes for booking configuration and public booking.

Assertions are on raw response bytes and JSON, never a parsed DOM.
"""
import contextlib
import os
import sqlite3
import pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod
from dashboard import practitioner_booking as pb

PID = "pid-mary"


@contextlib.contextmanager
def _open(path):
    """A Row-factory sqlite3 connection, closed on exit.

    The route code uses `db.connect(LOG_DB)` from dashboard.db; tests open the
    same file directly, matching tests/test_practitioner_drafts.py. A bare
    `import db` fails at COLLECTION in this suite -- verified -- so do not
    reach for one.
    """
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


CFG = {"timezone": "America/Anchorage", "office_hours": "1-5:09:00-17:00",
       "session_types": [{"slug": "intro", "label": "Free 20 minute intro call",
                          "duration_min": 20, "medium": "phone"}],
       "notice_hours": 24, "buffer_min": 0, "enabled": True}


@pytest.fixture
def logdb(tmp_path, monkeypatch):
    p = str(tmp_path / "log.db")
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    pb.init_tables(c)
    from dashboard import evox as _ev
    _ev.init_evox_tables(c)
    c.close()
    monkeypatch.setattr(appmod, "LOG_DB", p)
    return p


@pytest.fixture
def practitioner(monkeypatch, logdb):
    """Signed in as Mary."""
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: PID)
    # Roughly two dozen test files set TESTING=True on this SHARED app object
    # and never reset it, so whether the client converts an exception into a
    # 500 or re-raises depends on which files ran first. Pin it.
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    return appmod.app.test_client()


def test_config_requires_a_signed_in_practitioner(monkeypatch, logdb):
    monkeypatch.setattr(appmod, "_practitioner_session_pid", lambda: None)
    c = appmod.app.test_client()
    assert c.get("/api/practitioner/booking-config").status_code == 401
    assert c.post("/api/practitioner/booking-config", json=CFG).status_code == 401


def test_a_config_saves_and_reads_back(practitioner):
    r = practitioner.post("/api/practitioner/booking-config", json=CFG)
    assert r.status_code == 200, r.get_data(as_text=True)
    got = practitioner.get("/api/practitioner/booking-config").get_json()
    assert got["config"]["office_hours"] == "1-5:09:00-17:00"
    assert got["config"]["session_types"][0]["slug"] == "intro"


def test_an_invalid_config_is_rejected_with_a_readable_message(practitioner):
    bad = dict(CFG, office_hours="garbage")
    r = practitioner.post("/api/practitioner/booking-config", json=bad)
    assert r.status_code == 400
    assert "1-5:09:00-17:00" in r.get_json()["error"], \
        "the message should show the format, not name a regex"


def test_a_practitioner_cannot_write_another_practitioners_config(practitioner, logdb):
    """The pid comes from the session. A pid in the body must be ignored."""
    practitioner.post("/api/practitioner/booking-config",
                      json=dict(CFG, practitioner_id="pid-someone-else"))
    with _open(logdb) as c:
        assert pb.get_config(c, "pid-someone-else") is None
        assert pb.get_config(c, PID) is not None


def test_no_config_yet_reads_back_as_null_not_an_error(practitioner):
    r = practitioner.get("/api/practitioner/booking-config")
    assert r.status_code == 200
    assert r.get_json()["config"] is None


def test_the_form_page_serves_and_carries_the_workspace_nav(practitioner):
    body = practitioner.get("/practitioner/booking").get_data(as_text=True)
    assert 'class="workspace-nav"' in body
    assert "/practitioner/profile" in body
    assert "/practitioner/portal" in body


def test_the_form_states_that_external_calendars_are_not_read_yet():
    """3a offers slots from declared hours minus OUR bookings. A commitment
    that lives only in her Google Calendar will still be offered, and she has
    to be told that in plain words rather than discovering it."""
    import pathlib
    html = (pathlib.Path(appmod.STATIC) / "practitioner-booking.html").read_text()
    assert "Google Calendar" in html
    low = html.lower()
    assert "not" in low and "yet" in low


@pytest.mark.parametrize("page", ["practitioner-portal.html", "practitioner-dropship.html",
                                  "practitioner-settings.html", "practitioner-profile.html",
                                  "practitioner-booking.html"])
def test_every_workspace_page_links_to_booking(page):
    import pathlib, re
    html = (pathlib.Path(appmod.STATIC) / page).read_text()
    nav = re.search(r'<nav class="workspace-nav".*?</nav>', html, re.S)
    assert nav, f"{page} has no workspace nav"
    assert "/practitioner/booking" in nav.group(0), \
        f"{page} nav gives no way to reach the booking setup"
