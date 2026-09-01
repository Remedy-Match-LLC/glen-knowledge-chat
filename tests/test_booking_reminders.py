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
from datetime import datetime, timedelta, timezone
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


def test_a_practitioner_authored_location_is_escaped_in_the_email(
        client, logdb, _capture):
    """location and label are practitioner-authored free text on their way to
    a client's inbox. validate_config strips tags at write time, but
    get_config deliberately does NOT re-validate stored session types (a row
    saved before a field existed must still read back), so a row written by a
    migration or by hand can carry markup. Escape at render."""
    with _open(logdb) as c:
        c.execute("UPDATE practitioner_booking_config SET session_types=? "
                  "WHERE practitioner_id=?",
                  ('[{"slug": "intro", "label": "Intro", "duration_min": 20, '
                   '"medium": "zoom", "location": '
                   '"<img src=x onerror=alert(1)>"}]', PID))
        c.commit()
    _insert(logdb, email="esc@example.com", practitioner=PID,
            session_type="intro", medium="zoom")
    assert _run(client)["sent"] == 1
    html = _capture[-1]["html"]
    assert "<img" not in html
    assert "&lt;img" in html


def test_a_half_configured_smtp_shouts_instead_of_stamping_in_silence(
        client, logdb, monkeypatch, caplog):
    """SMTP_HOST set but the send still falling back to console-log means every
    client in the window is stamped as reminded with no email sent anywhere.
    reminded_at is one-way, so the real reminder can never fire afterwards.

    Deliberately a warning and not a refusal: in dev the console fallback IS
    the expected path, and refusing would stop the stamp there and in the
    existing test_evox_api reminder tests. The requirement is only that it
    cannot happen quietly, because the alternative way to find out is a client
    saying they were never reminded.
    """
    import logging
    import app as appmod
    _insert(logdb, email="silent@example.com", practitioner="rae",
            session_type="evox")
    monkeypatch.setattr(appmod, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(
        appmod, "send_evox_email",
        lambda *a, **k: ("console-log", "no email send mechanism configured"))
    with caplog.at_level(logging.ERROR):
        _run(client)
    assert any("console-log" in str(r.getMessage()) for r in caplog.records), \
        "a half-configured SMTP must be logged, not swallowed"


# ── the window itself: a real 24-48h band, measured in HER zone ────────────
#
# `start_ts` is naive wall-clock time in the PRACTITIONER's own zone. The cron
# used to build its window from `_hst_now()` and compare those naive strings
# directly, which is only correct when the practitioner's clock IS Hawaii's.
# For an Anchorage practitioner (1h ahead of Hawaii on AKST, 2h on AKDT) her
# clients' real lead time was 22-46h rather than 24-48h, and a band of
# appointments fell through the gap between consecutive daily runs entirely --
# never reminded at all, because nothing stamps a row the window skipped.

def _frozen(monkeypatch):
    """Freeze the cron's notion of 'now' to a real instant captured right here.

    Deliberately NOT a pinned calendar date. Three tests in this project once
    pinned a date a week out: green that day, red forever afterwards. Every
    expectation below is an OFFSET from this instant, so the test holds in
    January and in July, on both sides of a daylight-saving change.
    """
    t = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(appmod, "_reminder_now_utc", lambda: t)
    return t


def _hst_plus(instant, hours):
    """A naive wall-clock string `hours` after HAWAII's wall clock at `instant`.

    This is exactly the string the pre-fix cron treated as its window bound: it
    never knew whose clock the string belonged to.
    """
    hst = instant.astimezone(timezone(timedelta(hours=-10))).replace(tzinfo=None)
    return (hst + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _true_lead_hours(start_ts, tz_name, instant):
    """Real elapsed hours from `instant` to the appointment, reading `start_ts`
    as wall-clock time in `tz_name` -- the only lead a client experiences."""
    aware = datetime.fromisoformat(start_ts).replace(tzinfo=ZoneInfo(tz_name))
    return (aware - instant).total_seconds() / 3600.0


def test_an_alaska_booking_inside_the_hawaii_window_is_not_reminded_under_24_real_hours(
        client, logdb, _capture, monkeypatch):
    """LIVE defect. 24.5h on Hawaii's clock is 22.5h (AKDT) or 23.5h (AKST) of
    real lead for an Anchorage client. The old naive comparison reminded her a
    day and a half before a next-morning appointment, sometimes on the wrong
    calendar day. Under the real band she is left for tomorrow's run, and left
    UNSTAMPED so tomorrow's run can still reach her."""
    t = _frozen(monkeypatch)
    start = _hst_plus(t, 24.5)
    assert _true_lead_hours(start, HER_TZ, t) < 24
    _insert(logdb, email="early-ak@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start)
    assert _run(client)["sent"] == 0
    assert _capture == []
    assert _stamp(logdb, "early-ak@example.com") is None


def test_an_alaska_booking_past_the_hawaii_window_but_inside_the_real_band_is_reminded(
        client, logdb, _capture, monkeypatch):
    """The other half of the same defect, and the one that loses a client
    entirely. 48.5h on Hawaii's clock is 46.5-47.5h of real lead: inside the
    real 24-48h band, but past the old naive upper bound, so the old cron
    skipped it -- and skipping does not stamp, so tomorrow's run (by then under
    its lower bound) skipped it too. Nobody was ever reminded."""
    t = _frozen(monkeypatch)
    start = _hst_plus(t, 48.5)
    assert 24 <= _true_lead_hours(start, HER_TZ, t) <= 48
    _insert(logdb, email="late-ak@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start)
    assert _run(client)["sent"] == 1
    assert _capture[-1]["to"] == "late-ak@example.com"
    assert _stamp(logdb, "late-ak@example.com") is not None


def test_an_alaska_booking_squarely_inside_the_real_band_is_reminded(
        client, logdb, _capture, monkeypatch):
    """Control: the ordinary case both the old and the new window agree on."""
    t = _frozen(monkeypatch)
    start = _hst_plus(t, 30)
    assert 24 <= _true_lead_hours(start, HER_TZ, t) <= 48
    _insert(logdb, email="mid-ak@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=start)
    assert _run(client)["sent"] == 1
    assert _stamp(logdb, "mid-ak@example.com") is not None


def test_rae_and_glen_are_bit_for_bit_unchanged_by_the_zone_aware_window(
        client, logdb, _capture, monkeypatch):
    """Rae's and Glen's own bookings ARE Hawaii wall time, so the zone-aware
    band must reduce to exactly the window they have always had: 24-48h on the
    Hawaii clock, both edges. Three rows, one run: only the middle one is
    reminded, and the other two are left unstamped."""
    t = _frozen(monkeypatch)
    _insert(logdb, email="hi-early@example.com", practitioner="rae",
            session_type="evox", start_ts=_hst_plus(t, 23.5))
    _insert(logdb, email="hi-mid@example.com", practitioner="rae",
            session_type="evox", start_ts=_hst_plus(t, 30))
    _insert(logdb, email="hi-late@example.com", practitioner="rae",
            session_type="evox", start_ts=_hst_plus(t, 48.5))
    assert _run(client)["sent"] == 1
    assert [c["to"] for c in _capture] == ["hi-mid@example.com"]
    # The same expectations the existing EVOX control test asserts.
    assert "EVOX session" in _capture[-1]["html"]
    assert "HST" in _capture[-1]["html"]
    assert _stamp(logdb, "hi-mid@example.com") is not None
    assert _stamp(logdb, "hi-early@example.com") is None
    assert _stamp(logdb, "hi-late@example.com") is None


def test_glens_own_bookings_keep_the_same_hawaii_band(client, logdb, _capture,
                                                      monkeypatch):
    t = _frozen(monkeypatch)
    _insert(logdb, email="glen-early@example.com", practitioner="glen",
            session_type="triage", medium="video", start_ts=_hst_plus(t, 23.5))
    _insert(logdb, email="glen-mid@example.com", practitioner="glen",
            session_type="triage", medium="video", start_ts=_hst_plus(t, 30))
    assert _run(client)["sent"] == 1
    assert [c["to"] for c in _capture] == ["glen-mid@example.com"]
    assert "Glen" in _capture[-1]["html"]
    assert _stamp(logdb, "glen-early@example.com") is None


def test_a_booking_whose_zone_cannot_be_resolved_is_skipped_not_stamped(
        client, logdb, _capture, monkeypatch):
    """A config that reads back but carries a zone this machine cannot resolve
    gives no way to place the appointment in time. There is no safe default:
    guessing Hawaii would remind an Auckland client on the wrong day. Skip, and
    leave reminded_at NULL, because a reminder we cannot place today is one we
    must still be able to send tomorrow."""
    t = _frozen(monkeypatch)
    broken = dict(CFG, timezone="Mars/Olympus")
    monkeypatch.setattr(pb, "get_config",
                        lambda cx, pid: broken if pid == PID else None)
    _insert(logdb, email="nozone@example.com", practitioner=PID,
            session_type="intro", medium="zoom", start_ts=_hst_plus(t, 30))
    assert _run(client)["sent"] == 0
    assert _capture == []
    assert _stamp(logdb, "nozone@example.com") is None
