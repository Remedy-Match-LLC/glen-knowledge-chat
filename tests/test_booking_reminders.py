"""The daily reminder cron: /api/evox/run-reminders.

This route SENDS REAL EMAIL TO REAL CLIENTS. Every test here patches
`appmod.send_evox_email` at the module (see the autouse `_capture` fixture)
rather than relying on this environment happening to have no SMTP_HOST --
a call that is safe only because a credential is missing is not safe.

Two separate defects are pinned here:

  1. `triage` (the discovery-call flow, Rae's clients AND Glen's) had no
     branch of its own, so it fell to the EVOX else-clause and told the
     client "your EVOX session is tomorrow ... call Rae at {phone}" --
     the wrong session name, and Rae's phone number handed to Glen's
     discovery-call clients.

  2. A public practitioner's booking got no reminder at all, because the
     query was scoped `practitioner IN ('rae','glen')`.

`reminded_at` is a ONE-WAY stamp: once set, no later reminder can fire for
that booking, so a wrong reminder permanently suppresses the right one.
That is why several tests below assert on the stamp independently of what
was sent.
"""
import contextlib
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import evox as _ev
from dashboard import practitioner_booking as pb

PID = "pid-mary"
HER_NAME = "Mary Boyd"
HER_TZ = "America/Anchorage"
RAE_PHONE = "+18085550123"
LOCATION = "1200 Airport Way, Fairbanks AK"
LABEL = "Free 20 minute intro call"

CFG = {"timezone": HER_TZ, "office_hours": "1-5:09:00-17:00",
       "session_types": [{"slug": "intro", "label": LABEL,
                          "duration_min": 20, "medium": "zoom",
                          "location": LOCATION}],
       "notice_hours": 24, "buffer_min": 0, "enabled": True}

HDR = {"X-Console-Key": "test-secret"}


@contextlib.contextmanager
def _open(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _in_window():
    """A start_ts inside the cron's 24-48h window, as a naive wall-clock
    string. The cron compares these as strings against an HST-derived
    window; what each string MEANS is the practitioner's own zone."""
    return (appmod._hst_now() + timedelta(hours=30)).replace(
        microsecond=0, second=0).isoformat()


def _insert(path, **over):
    row = {"email": "client@example.com", "practitioner": "rae",
           "start_ts": _in_window(), "session_type": "evox",
           "medium": "phone", "visitor_tz": None}
    row.update(over)
    with _open(path) as c:
        _ev.init_evox_tables(c)
        cur = c.execute(
            "INSERT INTO evox_bookings (email,practitioner,start_ts,end_ts,"
            "status,prepaid,ics_uid,created_at,session_type,medium,visitor_tz) "
            "VALUES (?,?,?,?,'booked',0,?,?,?,?,?)",
            (row["email"], row["practitioner"], row["start_ts"], row["start_ts"],
             f"rem-{row['email']}@illtowell.com", "2026-01-01T00:00:00",
             row["session_type"], row["medium"], row["visitor_tz"]))
        c.commit()
        return cur.lastrowid


def _stamp(path, email):
    with _open(path) as c:
        r = c.execute("SELECT reminded_at FROM evox_bookings WHERE email=?",
                      (email,)).fetchone()
        return r["reminded_at"] if r else None


@pytest.fixture
def logdb(tmp_path, monkeypatch):
    p = str(tmp_path / "log.db")
    with _open(p) as c:
        pb.init_tables(c)
        _ev.init_evox_tables(c)
        pb.set_config(c, PID, CFG)
    monkeypatch.setattr(appmod, "LOG_DB", p)
    return p


@pytest.fixture(autouse=True)
def _capture(monkeypatch):
    """Every send in this module lands here, never in smtplib."""
    calls = []

    def _fake(to, name, subject, html_body, text_body, ics_bytes):
        calls.append({"to": to, "subject": subject, "html": html_body,
                      "text": text_body})
        return ("console-log", None)

    monkeypatch.setattr(appmod, "send_evox_email", _fake)
    return calls


@pytest.fixture
def client(monkeypatch, logdb):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(appmod, "EVOX_RAE_PHONE", RAE_PHONE, raising=False)
    monkeypatch.setattr(appmod, "_practitioner_display_name",
                        lambda pid: HER_NAME if pid == PID else None)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    # Two dozen other files set TESTING on this SHARED app object and never
    # reset it. Pin it so behaviour does not depend on collection order.
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    return appmod.app.test_client()


def _run(client):
    r = client.post("/api/evox/run-reminders", headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


# ── defect 1: triage must never be worded as an EVOX session with Rae ──────

def test_glens_triage_client_is_never_given_raes_phone_number(client, logdb, _capture):
    """LIVE defect. A triage booking with Glen fell to the else-clause and
    was told "your EVOX session ... call Rae at {phone}"."""
    _insert(logdb, email="triage-glen@example.com", practitioner="glen",
            session_type="triage", medium="video")
    assert _run(client)["sent"] == 1
    body = _capture[-1]["html"] + _capture[-1]["subject"] + (_capture[-1]["text"] or "")
    assert RAE_PHONE not in body, "Glen's discovery-call client must never get Rae's number"
    assert "Rae" not in body, "this call is with Dr. Glen, not Rae"
    assert "EVOX session" not in body, "a discovery call is not an EVOX session"
    assert "Glen" in body


def test_raes_triage_client_gets_her_call_worded_as_a_call_not_an_evox_session(
        client, logdb, _capture):
    """The same session type, booked with Rae. Her number IS the right
    number here; the session name still must not say EVOX."""
    _insert(logdb, email="triage-rae@example.com", practitioner="rae",
            session_type="triage", medium="phone")
    assert _run(client)["sent"] == 1
    body = _capture[-1]["html"] + _capture[-1]["subject"]
    assert "EVOX session" not in body
    assert "Rae" in body and RAE_PHONE in body


def test_an_evox_booking_still_gets_the_evox_reminder(client, logdb, _capture):
    """Control: the else-clause still exists for what it was written for."""
    _insert(logdb, email="evox@example.com", practitioner="rae",
            session_type="evox")
    assert _run(client)["sent"] == 1
    assert "EVOX session" in _capture[-1]["html"]
    assert "HST" in _capture[-1]["html"], "Rae's own bookings are Hawaii time"


# ── defect 2: a public booking is reminded, correctly ──────────────────────

def test_a_public_booking_is_reminded_with_her_name_session_and_location(
        client, logdb, _capture):
    _insert(logdb, email="pubclient@example.com", practitioner=PID,
            session_type="intro", medium="zoom")
    assert _run(client)["sent"] == 1
    c = _capture[-1]
    assert c["to"] == "pubclient@example.com"
    body = c["html"] + c["subject"]
    assert HER_NAME in body
    assert LABEL in body
    assert LOCATION in c["html"]
    assert RAE_PHONE not in body and "Rae" not in body
    assert "EVOX" not in body
    assert _stamp(logdb, "pubclient@example.com") is not None


def test_a_public_reminder_uses_the_clients_own_stored_timezone(
        client, logdb, _capture):
    """visitor_tz is what the client's browser reported at booking time.
    The reminder must state the appointment in it, converted -- not merely
    relabelled -- and must never say HST for an Alaska practitioner."""
    start = _in_window()
    _insert(logdb, email="nz@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start,
            visitor_tz="Pacific/Auckland")
    assert _run(client)["sent"] == 1
    expect = (datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(HER_TZ))
              .astimezone(ZoneInfo("Pacific/Auckland")))
    html = _capture[-1]["html"]
    assert expect.strftime("%Y-%m-%d %H:%M") in html
    assert expect.strftime("%Z") in html, "label the zone actually used"
    assert "HST" not in html


def test_a_public_reminder_falls_back_to_her_zone_when_the_booking_has_none(
        client, logdb, _capture):
    """Every row that predates visitor_tz has none. Her configured zone is
    the honest fallback, and its own label -- not HST."""
    start = _in_window()
    _insert(logdb, email="notz@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start,
            visitor_tz=None)
    assert _run(client)["sent"] == 1
    expect = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(HER_TZ))
    html = _capture[-1]["html"]
    assert expect.strftime("%Y-%m-%d %H:%M") in html
    assert expect.strftime("%Z") in html   # AKST or AKDT, never HST
    assert "HST" not in html


def test_a_broken_stored_timezone_falls_back_and_is_never_shown(
        client, logdb, _capture):
    """visitor_tz is stored RAW and is not validated at write time, so a
    broken browser can have stored anything. Validate before use, and never
    label a time with a zone that was not used."""
    start = _in_window()
    _insert(logdb, email="mars@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start,
            visitor_tz="Mars/Olympus")
    assert _run(client)["sent"] == 1
    expect = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(HER_TZ))
    html = _capture[-1]["html"]
    assert "Mars" not in html
    assert expect.strftime("%Y-%m-%d %H:%M") in html
    assert expect.strftime("%Z") in html


def test_a_booking_whose_practitioner_has_no_config_is_skipped_not_stamped(
        client, logdb, _capture):
    """No config means no timezone, no session label and no location: there
    is nothing correct to say. Sending the EVOX else-clause would be worse
    than silence, and STAMPING it would suppress the right reminder forever."""
    _insert(logdb, email="orphan@example.com", practitioner="pid-nobody",
            session_type="intro", medium="zoom")
    assert _run(client)["sent"] == 0
    assert _capture == []
    assert _stamp(logdb, "orphan@example.com") is None


# ── the one-way stamp ─────────────────────────────────────────────────────

def test_a_failed_send_does_not_stamp_reminded_at(client, logdb, monkeypatch,
                                                  _capture):
    """reminded_at is one-way. Stamping a send that raised would mean this
    client is never reminded at all."""
    def _boom(*a, **k):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(appmod, "send_evox_email", _boom)
    _insert(logdb, email="fail@example.com", practitioner=PID,
            session_type="intro", medium="zoom")
    assert _run(client)["sent"] == 0
    assert _stamp(logdb, "fail@example.com") is None

    # And the next run, with a working send, still reaches her.
    calls = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda *a, **k: calls.append(a[0]) or ("console-log", None))
    assert _run(client)["sent"] == 1
    assert calls == ["fail@example.com"]


def test_an_already_reminded_booking_is_not_reminded_twice(client, logdb,
                                                           _capture):
    _insert(logdb, email="once@example.com", practitioner=PID,
            session_type="intro", medium="zoom")
    assert _run(client)["sent"] == 1
    first = _stamp(logdb, "once@example.com")
    assert first is not None
    assert _run(client)["sent"] == 0
    assert len(_capture) == 1
    assert _stamp(logdb, "once@example.com") == first


def test_a_booking_outside_the_window_is_left_alone(client, logdb, _capture):
    _insert(logdb, email="soon@example.com", practitioner=PID,
            session_type="intro", medium="zoom",
            start_ts=(appmod._hst_now() + timedelta(hours=2)).isoformat())
    assert _run(client)["sent"] == 0
    assert _capture == []
    assert _stamp(logdb, "soon@example.com") is None
