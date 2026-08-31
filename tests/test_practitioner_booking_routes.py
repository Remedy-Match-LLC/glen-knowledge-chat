"""Routes for booking configuration and public booking.

Assertions are on raw response bytes and JSON, never a parsed DOM.
"""
import contextlib
import os
import shutil
import sqlite3
import subprocess
from unittest import mock
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


PRACTITIONER_EMAIL = "her@example.com"


def _book_one(client, slug="mary-boyd"):
    """Book the first offered slot. Returns the POST response."""
    slot = client.get(f"/api/book/{slug}/slots?session=intro").get_json()["slots"][0]
    return client.post(f"/api/book/{slug}", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})


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


@pytest.fixture(autouse=True)
def _stub_send_evox_email(monkeypatch):
    """A booking POST in this file sends TWO real emails on success (the
    client confirmation, and the practitioner notification) via
    appmod.send_evox_email. The large majority of the tests below exercise
    that route without patching it themselves -- inert only by the accident
    that this dev Doppler environment has no SMTP_HOST set, so
    send_evox_email raises inside a caller-side try/except and the exception
    is swallowed. Setting SMTP_HOST would make every one of those tests
    reach real smtplib.sendmail() and bounce real messages off Glen's
    sending domain.

    Autouse + function-scoped so it applies to every test in this module
    (including ones added later) without each one asking for it, and stacks
    correctly with the handful of tests below that monkeypatch
    send_evox_email again themselves to inspect what was sent -- monkeypatch
    applies patches in call order and undoes them in reverse at teardown, so
    a test's own later setattr simply shadows this one for its duration.
    Safe by construction, not by the accident of an unset env var.
    """
    monkeypatch.setattr(
        appmod, "send_evox_email",
        lambda to, name, subject, html_body, text_body, ics_bytes: None)


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


def test_a_saved_timezone_outside_the_option_list_survives_the_round_trip():
    """A curated fallback list -- and even Intl.supportedValuesOf(), which
    varies by browser/OS -- can miss the zone she actually saved from a
    different browser. Setting <select>.value to a zone with no matching
    <option> silently lands on selectedIndex -1, which reads back as "" and
    400s at save with no way to pick her real zone again. The fix has to
    insert the saved zone into the option list BEFORE assigning sel.value,
    the same way the browser's own guess is already merged in.

    Assertions are on the raw JS source, per this file's convention (see
    module docstring): there is no DOM here to drive.
    """
    import pathlib, re
    html = (pathlib.Path(appmod.STATIC) / "practitioner-booking.html").read_text()

    # The merge must happen keyed off c.timezone (the saved config's zone,
    # read from the GET response), checked against the current option list,
    # and inserted before sel.value is assigned -- in that order, in one
    # contiguous block. Order matters: merging after the assignment would be
    # too late, selectedIndex would already be -1.
    merge = re.search(
        r"c\.timezone\s*&&\s*zones\.indexOf\(c\.timezone\)\s*<\s*0"
        r".*?zones\.unshift\(c\.timezone\)"
        r".*?sel\.value\s*=\s*c\.timezone;",
        html, re.S)
    assert merge, (
        "no code path merges the saved config's timezone into the option "
        "list before selecting it -- a zone outside the (curated or "
        "Intl.supportedValuesOf) list will silently fail to select")

    # The same identity applies to the pre-fill path (no config yet): a
    # server-suggested or browser-guessed default that isn't already in the
    # list must also be inserted before use.
    prefill = re.search(
        r"pre\s*&&\s*zones\.indexOf\(pre\)\s*<\s*0"
        r".*?zones\.unshift\(pre\)"
        r".*?sel\.value\s*=\s*pre;",
        html, re.S)
    assert prefill, "the pre-fill guess/default path has the same gap"

    # Belt-and-braces: even if the merge above has some other gap, Save must
    # refuse to send an empty timezone rather than let the server 400 her.
    assert "!payload.timezone" in html or "!payload.timezone)" in html, \
        "save() has no guard against sending a blank timezone"


def test_an_unauthenticated_visitor_sees_the_auth_wall_not_the_form():
    """The page itself is static and serves to anyone; only the config API
    is authenticated. Without a client-side check, a 401 body (no "config"
    key) renders identically to "signed in, first time, nothing saved yet" --
    so a signed-out visitor sees what looks like a working form and only
    discovers otherwise by filling it in and clicking Save.
    """
    import pathlib, re
    html = (pathlib.Path(appmod.STATIC) / "practitioner-booking.html").read_text()

    assert 'id="auth-wall"' in html, "no auth-wall element to show"
    assert 'id="main-content"' in html and 'style="display:none"' in html, \
        "the form must start hidden until we know the visitor is signed in"

    # The load() handler must branch on the RESPONSE STATUS (401 / !r.ok),
    # not on whether the JSON body happens to have a "config" key -- the
    # 401 body has no such key, but neither does a legitimate first-time
    # 200. Checking the body shape instead of the status is exactly the bug.
    gate = re.search(
        r"r\.status === 401[^)]*\)\s*\{\s*showAuthWall\(\)",
        html, re.S)
    assert gate, (
        "load() does not gate on the 401 status before revealing the form "
        "-- it would show the empty-config form to a signed-out visitor")


# --- Task 4: the public booking route -------------------------------------
from datetime import date, timedelta  # noqa: E402


def _seed_slug(logdb, slug="mary-boyd", email="my_mary_boyd@example.com"):
    with _open(logdb) as c:
        # organization is here (nullable, unset by every other caller of this
        # helper) only so dashboard.public_surface.build_practitioner_storefront
        # -- which selects it -- can run unmocked against this row, as
        # test_the_book_link_on_the_public_page_points_at_a_real_route does.
        c.execute("""CREATE TABLE IF NOT EXISTS affiliate_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
            slug TEXT, status TEXT, organization TEXT)""")
        c.execute("INSERT INTO affiliate_signups (name,email,slug,status) "
                  "VALUES (?,?,?,'approved')", ("Mary Boyd", email, slug))
        c.commit()


@pytest.fixture
def public(monkeypatch, logdb):
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    from dashboard import practitioner_booking as _pb
    monkeypatch.setattr(_pb, "resolve_practitioner_pid", lambda cx, slug: PID)
    # /api/book/<slug> POST is now velocity-guarded (CRITICAL 3). appmod's
    # _chat_velocity limiter is process-global/module-level state shared
    # across every test in this file -- without a fresh one per test, the
    # Flask test client's fixed remote_addr means every test's POSTs share
    # ONE counter across the whole file, so later tests silently start
    # getting 429s from an EARLIER test's usage rather than exercising the
    # booking logic they actually claim to test. Same isolation approach as
    # tests/test_chat_velocity.py's own velocity_app fixture.
    from dashboard.chat_limits import VelocityLimiter
    monkeypatch.setattr(appmod, "_chat_velocity", VelocityLimiter())
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id", lambda pid: PRACTITIONER_EMAIL)
    monkeypatch.setattr(_pp, "practitioner_phone_by_id", lambda pid: "+15550100")
    return appmod.app.test_client()


def test_slots_are_public_and_need_no_token(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/api/book/mary-boyd/slots?session=intro&tz=Pacific/Auckland")
    assert r.status_code == 200
    assert isinstance(r.get_json()["slots"], list)


def test_slots_are_rendered_in_the_visitor_timezone(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/api/book/mary-boyd/slots?session=intro&tz=Pacific/Auckland")
    slots = r.get_json()["slots"]
    assert slots, "expected some availability"
    # An offset-bearing string, not a naive one, so the browser cannot guess wrong.
    assert "+" in slots[0]["visitor"] or "-" in slots[0]["visitor"][10:]
    assert slots[0]["start"] != slots[0]["visitor"]


def test_a_practitioner_who_has_not_enabled_booking_offers_nothing(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, enabled=False))
    r = public.get("/api/book/mary-boyd/slots?session=intro")
    assert r.status_code == 200
    assert r.get_json()["slots"] == []


def test_booking_writes_a_row_and_returns_a_cancel_token(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["cancel_token"]
    with _open(logdb) as c:
        row = c.execute("SELECT practitioner, start_ts, status FROM evox_bookings").fetchone()
        assert row["practitioner"] == PID and row["status"] == "booked"


def test_the_public_book_route_refuses_after_the_ip_cap(public, logdb, monkeypatch):
    """CRITICAL: this route was unauthenticated, unrated, and sends mail as
    Glen's SMTP identity on every success -- nothing capped it, so one
    attacker could fill a whole practitioner's grid (~500 slots across the
    21-day window) and fire ~500 outbound messages. The fix reuses the exact
    guard/tier/pattern /begin/fireside/agent already uses for its own public
    unauthenticated POST (_velocity_guard(request, "anonymous", ...)),
    keyed on IP.

    Mirrors tests/test_chat_velocity.py's own integration pattern: a fresh
    limiter + tightened per_min so the test does not need to fire 10+ real
    requests, budget exhausted via direct _velocity_guard calls (no DB I/O),
    then ONE real POST from the same IP to prove the guard is actually wired
    into this route -- and that a blocked request never reaches
    create_booking (no row written).
    """
    from dashboard.chat_limits import VelocityLimiter
    tight = dict(appmod.LIMITS)
    tight["anonymous"] = dict(tight["anonymous"], per_min=2)
    monkeypatch.setattr(appmod, "LIMITS", tight)
    monkeypatch.setattr(appmod, "_chat_velocity", VelocityLimiter())

    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]

    ip = "203.0.113.77"
    with appmod.app.test_request_context("/api/book/mary-boyd",
                                         headers={"X-Forwarded-For": ip}):
        from flask import request as _rq
        assert appmod._velocity_guard(_rq, "anonymous") is None  # hit 1
        assert appmod._velocity_guard(_rq, "anonymous") is None  # hit 2

    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Attacker", "email": "attacker@example.com"},
        headers={"X-Forwarded-For": ip})
    assert r.status_code == 429
    with _open(logdb) as c:
        n = c.execute("SELECT COUNT(*) c FROM evox_bookings").fetchone()
        assert n["c"] == 0, "a rate-limited request must never reach create_booking"


def test_the_clients_name_is_stored_not_just_validated(public, logdb):
    """name is required and validated at the door, then must actually reach
    the row -- create_booking has no name parameter and evox_bookings had no
    column for it, so a practitioner opening her calendar saw an email
    address and nothing else. The public route stores it with a targeted
    UPDATE after create_booking returns, inside the same connection, rather
    than changing create_booking's signature (Glen's and Rae's live flows
    call it)."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Priya Chandrasekaran", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    with _open(logdb) as c:
        row = c.execute("SELECT client_name FROM evox_bookings").fetchone()
        assert row["client_name"] == "Priya Chandrasekaran"
        # The calendar summary must carry all three parts: which kind of
        # session (the practitioner's own configured label, not a hardcoded
        # one), who, and how to reach them. Dropping the label would leave
        # Mary looking at a name with no idea whether it's a twenty-minute
        # intro or a full session.
        cal = c.execute("SELECT summary FROM calendar_events").fetchone()
        assert "Free 20 minute intro call" in cal["summary"]
        assert "Priya Chandrasekaran" in cal["summary"]
        assert "client@example.com" in cal["summary"]


class _BoomOnClientNameUpdate:
    """Wraps a real sqlite3 connection and raises on the client_name UPDATE
    only, forwarding everything else untouched. sqlite3.Connection is an
    immutable extension type in this Python version -- monkeypatching
    Connection.execute directly raises 'cannot set attribute of immutable
    type' -- so the route's own connection has to be swapped out at
    db.connect() instead of patched in place."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *a, **kw):
        if sql.startswith("UPDATE evox_bookings SET client_name"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *a, **kw)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, attr):
        return getattr(self._real, attr)

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._real.row_factory = value


def test_a_failed_post_booking_update_does_not_fail_the_booking(public, logdb, monkeypatch):
    """create_booking already committed by the time the client_name/summary
    UPDATEs run. If either raises, the booking is still real -- the visitor
    must get a 200 with a valid cancel_token, not a 500 for a slot that is
    in fact theirs. A false failure here is the shape that makes a client
    book twice or email the practitioner asking whether it worked."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]

    real_db_connect = pb.db.connect
    monkeypatch.setattr(appmod.db, "connect",
                        lambda path, **kw: _BoomOnClientNameUpdate(real_db_connect(path, **kw)))
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["cancel_token"]
    monkeypatch.undo()
    with _open(logdb) as c:
        row = c.execute("SELECT practitioner, start_ts, status FROM evox_bookings").fetchone()
        assert row["practitioner"] == PID and row["status"] == "booked"


def test_the_same_slot_cannot_be_booked_twice(public, logdb):
    """The guard is the database UNIQUE index on (practitioner, start_ts), so
    it holds across processes. Exactly one booking survives.

    CORRECTED from the brief (which asserted second.status_code == 409):
    these two POSTs run sequentially through the same test client, not
    concurrently. The first commits before the second's own availability
    check runs, so the second's fresh `booked_starts` read already contains
    it and the readable pre-check ("start_ts not in offered") is what stops
    it -- 400 slot_unavailable, the same path a bogus timestamp takes. That
    pre-check existing at all is correct and required by the spec ("check
    first for a readable error"). The 409-from-SlotTaken path is for the
    genuine race the check cannot see -- two requests whose pre-checks both
    run before either has committed -- which a single-threaded sequential
    test client cannot produce. That path is exercised by the mutation test
    in the task report instead: drop the UNIQUE index and confirm two rows
    would survive even with the pre-check still in place, which is what
    proves the index carries weight the check does not.
    """
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    body = {"session": "intro", "start": slot["start"],
            "name": "A Client", "email": "client@example.com"}
    first = public.post("/api/book/mary-boyd", json=body)
    second = public.post("/api/book/mary-boyd", json=dict(body, email="other@example.com"))
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.get_json()["error"] == "slot_unavailable"
    with _open(logdb) as c:
        n = c.execute("SELECT COUNT(*) c FROM evox_bookings WHERE status='booked'").fetchone()
        assert n["c"] == 1


def test_a_genuine_race_is_caught_by_the_unique_index_not_the_check(public, logdb):
    """The sequential test above can never reach the SlotTaken/409 branch,
    because its own pre-check (fresh `booked_starts`) already sees the first
    commit. To exercise the branch that a TRUE race would hit -- two
    requests whose pre-checks both ran before either committed -- patch
    `slots_for` to keep reporting the slot as offered even after it is
    booked, standing in for that race window. The insert must still refuse
    via the UNIQUE index, caught as SlotTaken, and returned as 409 -- proving
    the check alone would have let this through.
    """
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    body = {"session": "intro", "start": slot["start"],
            "name": "A Client", "email": "client@example.com"}
    first = public.post("/api/book/mary-boyd", json=body)
    assert first.status_code == 200

    from dashboard import practitioner_booking as _pb
    real_slots_for = _pb.slots_for

    def _stale_offered(cx, pid, *, days, session_slug, booked, busy=()):
        # Simulate the race: report the slot as still offered, as a second
        # request's pre-check would if it ran before the first committed.
        return real_slots_for(cx, pid, days=days, session_slug=session_slug,
                              booked=set(), busy=busy) or [slot["start"]]

    with mock.patch.object(_pb, "slots_for", side_effect=_stale_offered):
        second = public.post("/api/book/mary-boyd",
                             json=dict(body, email="other@example.com"))
    assert second.status_code == 409
    assert second.get_json()["error"] == "slot_taken"
    with _open(logdb) as c:
        n = c.execute("SELECT COUNT(*) c FROM evox_bookings WHERE status='booked'").fetchone()
        assert n["c"] == 1


def test_a_slot_outside_the_offered_set_is_refused(public, logdb):
    """Never trust a start time from the request. A client can post anything."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": "2026-09-07T03:00:00",
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "slot_unavailable"


def test_a_bad_email_is_refused(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "not-an-email"})
    assert r.status_code == 400


def test_an_unknown_slug_is_404(public, monkeypatch):
    from dashboard import practitioner_booking as _pb
    monkeypatch.setattr(_pb, "resolve_practitioner_pid", lambda cx, slug: None)
    assert public.get("/api/book/nobody/slots?session=intro").status_code == 404


# --- resolve_practitioner_pid itself, not the monkeypatched stand-in --------
# Every test above replaces this function; none of them exercise it. It is
# the thing that decides WHICH practitioner's calendar a public write lands
# in, so it needs its own coverage against a real seeded row and the real
# find_practitioner_id_by_email.

def test_resolve_practitioner_pid_happy_path(logdb, monkeypatch):
    _seed_slug(logdb, slug="mary-boyd", email="mary@example.com")
    from dashboard import practitioner_portal as pp
    monkeypatch.setattr(pp, "find_practitioner_id_by_email",
                        lambda email: PID if email == "mary@example.com" else None)
    with _open(logdb) as c:
        assert pb.resolve_practitioner_pid(c, "mary-boyd") == PID


def test_resolve_practitioner_pid_refuses_a_non_approved_slug(logdb, monkeypatch):
    """The status='approved' filter is what stops an unapproved or revoked
    practitioner from taking public bookings. Asserted nowhere else."""
    with _open(logdb) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS affiliate_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
            slug TEXT, status TEXT)""")
        c.execute("INSERT INTO affiliate_signups (name,email,slug,status) "
                  "VALUES (?,?,?,?)",
                  ("Mary Boyd", "mary@example.com", "mary-boyd", "pending"))
        c.commit()
    from dashboard import practitioner_portal as pp
    monkeypatch.setattr(pp, "find_practitioner_id_by_email", lambda email: PID)
    with _open(logdb) as c:
        assert pb.resolve_practitioner_pid(c, "mary-boyd") is None


def test_resolve_practitioner_pid_unknown_slug_is_none(logdb, monkeypatch):
    _seed_slug(logdb, slug="mary-boyd", email="mary@example.com")
    from dashboard import practitioner_portal as pp
    monkeypatch.setattr(pp, "find_practitioner_id_by_email", lambda email: PID)
    with _open(logdb) as c:
        assert pb.resolve_practitioner_pid(c, "nobody") is None


def test_resolve_practitioner_pid_missing_table_fails_closed(logdb):
    """No affiliate_signups table at all (the fresh `logdb` fixture never
    creates one) must read as 'no such practitioner', not 500 a public page."""
    with _open(logdb) as c:
        assert pb.resolve_practitioner_pid(c, "mary-boyd") is None


def test_resolve_practitioner_pid_supabase_down_fails_closed(logdb, monkeypatch):
    """The real find_practitioner_id_by_email, not a stand-in. In this
    environment SUPABASE_DB_URL is unset, so db_supabase.supabase_cursor()
    raises RuntimeError('SUPABASE_DB_URL env var is not set') -- that IS the
    'Supabase is down' case, unmonkeypatched. A public route must 404, not
    500, when the practitioner directory it depends on is unreachable."""
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    _seed_slug(logdb, slug="mary-boyd", email="mary@example.com")
    with _open(logdb) as c:
        assert pb.resolve_practitioner_pid(c, "mary-boyd") is None


def test_the_gated_consult_route_is_untouched():
    """The spec forbids a bypass on /api/consult/book. This asserts the source
    still contains its checks, so a future 'small refactor' that shares a
    helper with the public route cannot quietly remove them."""
    import inspect
    src = inspect.getsource(appmod)
    i = src.index('"/api/consult/book"')
    window = src[i:i + 4000]
    assert "intake_required" in window
    assert "not_ready" in window


def test_the_rendered_timezone_reflects_the_actual_fallback_not_the_request(public, logdb):
    """to_visitor_tz falls back silently to the practitioner's zone on an
    unusable visitor zone and tells its caller nothing. A visitor whose
    browser reports a broken zone must not read times labelled with what
    THEY sent -- the route must resolve the effective zone once and report
    THAT, not request.args['tz'], so the page can label honestly."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/api/book/mary-boyd/slots?session=intro&tz=Mars/Olympus")
    assert r.status_code == 200
    body = r.get_json()
    assert body["rendered_timezone"] == CFG["timezone"]
    assert body["rendered_timezone"] != "Mars/Olympus"


# --- Task 5: confirmation email and the cancel link ------------------------

def test_booking_sends_a_confirmation_to_the_client(public, logdb, monkeypatch):
    """The client has no account, so the confirmation email -- sent via
    send_evox_email (the same sibling send path the other EVOX confirmation
    flows use, since it is the one that can carry a calendar attachment) --
    is their only record of the appointment. text_body is what the
    assertions below check, mirroring the plain-text side of the message a
    client actually reads."""
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append({"to": to, "subject": subject,
                                    "body": text_body, "ics": ics_bytes}))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert sent, "the client got no confirmation"
    body = sent[0]["body"]
    assert "cancel" in body.lower()
    assert "/book/cancel?" in body


def test_the_practitioner_is_notified_of_a_new_booking(public, logdb, monkeypatch):
    """CRITICAL: both sibling flows notify the practitioner
    (_evox_send_confirmations -> Rae, _consult_send_confirmations -> Glen).
    This route only emailed the client -- Mary could enable booking, a
    stranger takes her Tuesday 9am, and she finds out when they call. Her
    address comes from the practitioner record the slug already resolved to
    (pid), never from anything the visitor submitted, and the time is shown
    in HER OWN configured zone (CFG["timezone"]), not the visitor's."""
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id",
                        lambda pid: "mary@example.com" if pid == PID else "")
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append({"to": to, "subject": subject, "body": text_body}))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    assert len(sent) == 2, \
        "expected both the client confirmation and the practitioner notification"
    to_addrs = {m["to"] for m in sent}
    assert "client@example.com" in to_addrs
    assert "mary@example.com" in to_addrs
    note = next(m for m in sent if m["to"] == "mary@example.com")
    assert "A Client" in note["body"]
    assert "client@example.com" in note["body"]
    # Her own zone, not the visitor's Pacific/Auckland -- start_ts is already
    # naive practitioner-local wall time, so it must appear UNCONVERTED.
    assert CFG["timezone"] in note["body"]
    assert slot["start"].replace("T", " ") in note["body"]


def test_a_newline_in_the_visitors_name_does_not_suppress_the_notification(
        public, logdb, monkeypatch):
    """name is only .strip()[:120]'d before it reaches here, so an interior
    newline survives into the subject line built for the practitioner's
    notification. Python's email package then refuses (not injects) a
    header value containing an embedded newline -- HeaderParseError inside
    send_evox_email's own msg["Subject"] = subject line -- and that send is
    wrapped in a try/except that swallows it, so the booking still returns
    200, the client still gets confirmed, and the practitioner is never
    told. Asserting the send merely happened would not catch this: the stub
    below is a no-op recorder, so it "sends" either way regardless of what
    subj contains. The regression is only visible by inspecting the actual
    subject string the route handed to send_evox_email."""
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id",
                        lambda pid: "mary@example.com" if pid == PID else "")
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append({"to": to, "subject": subject}))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Evil\nX-Injected: header", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    note = next(m for m in sent if m["to"] == "mary@example.com")
    assert "\n" not in note["subject"], \
        "a newline here would make smtplib refuse the whole send"
    assert "\r" not in note["subject"]


def test_a_failed_practitioner_notification_does_not_fail_the_booking(
        public, logdb, monkeypatch):
    """create_booking already committed by the time this send runs. If it
    raises, the booking is still real -- the client must still get a 200
    with a valid cancel_token, and their own confirmation must still go
    out."""
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id",
                        lambda pid: "mary@example.com" if pid == PID else "")
    sent = []

    def _boom_for_practitioner(to, name, subject, html_body, text_body, ics_bytes):
        if to == "mary@example.com":
            raise RuntimeError("smtp down")
        sent.append(to)
    monkeypatch.setattr(appmod, "send_evox_email", _boom_for_practitioner)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["cancel_token"]
    assert sent == ["client@example.com"], "the client confirmation must still be sent"
    with _open(logdb) as c:
        row = c.execute("SELECT status FROM evox_bookings").fetchone()
        assert row["status"] == "booked"


def test_no_practitioner_email_on_file_skips_notification_without_failing(
        public, logdb, monkeypatch):
    """practitioner_email_by_id fails closed to '' (Supabase down, or no
    matching row) -- that must not be treated as an address to mail, and
    must not break the booking or the client's own confirmation."""
    from dashboard import practitioner_portal as _pp
    monkeypatch.setattr(_pp, "practitioner_email_by_id", lambda pid: "")
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append(to))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert r.status_code == 200
    assert sent == ["client@example.com"]


def test_the_confirmation_states_the_time_in_the_visitor_timezone(public, logdb, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append(text_body))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    assert "Pacific/Auckland" in sent[0] or "NZ" in sent[0], \
        "a client cannot act on a time in a zone they do not live in"


def test_the_confirmation_includes_a_calendar_invite_matching_the_booking(
        public, logdb, monkeypatch):
    """The task title promises a calendar invite, not just a plain-text
    confirmation, so the .ics must actually be built and attached.

    Same-zone case: no `tz` in the request, so effective_visitor_tz falls
    back to the practitioner's own zone (CFG["timezone"] = America/Anchorage).
    Asserts DTSTART is the correct UTC instant for the booking, computed
    independently via ZoneInfo -- NOT a naive string-equality check against
    slot["start"] (round 1's version of this test did exactly that, and it
    would have passed with the timezone handling completely broken, because
    a floating VEVENT built from the practitioner's own naive clock happens
    to look identical to slot["start"] when no visitor tz was given). The
    dedicated cross-zone test below is what actually exercises the case a
    floating VEVENT gets wrong.
    """
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append(ics_bytes))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    assert sent and sent[0], "no calendar invite was built"
    ics_text = sent[0].decode("utf-8")
    assert "BEGIN:VEVENT" in ics_text
    import re as _re
    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo as _ZI
    m = _re.search(r"DTSTART:(\d{8}T\d{6}Z)", ics_text)
    assert m, f"DTSTART is not in RFC 5545 UTC (Z-suffixed) form: {ics_text}"
    naive_local = _dt.fromisoformat(slot["start"][:19])
    expected = naive_local.replace(tzinfo=_ZI(CFG["timezone"])) \
        .astimezone(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    assert m.group(1) == expected


def test_the_confirmation_invite_uses_the_correct_utc_instant_across_zones(
        public, logdb, monkeypatch):
    """The bug this fix exists to close: the confirmation's text body already
    converts to the visitor's own zone via to_visitor_tz, but a floating
    (no-Z, no-TZID) VEVENT is defined by RFC 5545 to be interpreted in the
    VIEWER's zone -- so a client in Auckland booking an Anchorage
    practitioner would read the RIGHT time in the email text and add the
    WRONG time to their calendar from the attached invite. DTSTART must be
    the real UTC instant, and must specifically NOT equal the naive
    practitioner-local wall-clock string with a bare Z appended (which is
    what the old floating-time bug would produce if naively patched)."""
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append(ics_bytes))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)  # CFG["timezone"] == "America/Anchorage"
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    ics_text = sent[0].decode("utf-8")
    import re as _re
    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo as _ZI
    m = _re.search(r"DTSTART:(\d{8}T\d{6}Z)", ics_text)
    assert m, f"DTSTART is not in RFC 5545 UTC (Z-suffixed) form: {ics_text}"
    naive_local = _dt.fromisoformat(slot["start"][:19])
    expected_utc = naive_local.replace(tzinfo=_ZI(CFG["timezone"])) \
        .astimezone(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    assert m.group(1) == expected_utc
    naive_with_bare_z = naive_local.strftime("%Y%m%dT%H%M%S") + "Z"
    assert m.group(1) != naive_with_bare_z, \
        "DTSTART must be a real UTC conversion, not the naive practitioner-local time with a bare Z appended"


def test_build_ics_with_no_tz_name_is_byte_identical_to_today():
    """tz_name is additive, not a behavior change to the default path. Five
    OTHER callers of build_ics (Glen's, Rae's, the masterclass and consult
    flows) depend on today's floating-VEVENT output and are not in scope
    for this change, so omitting tz_name must reproduce that output byte
    for byte. Pinned from the function's actual output, captured before any
    DTSTART/DTEND change was made."""
    from dashboard import evox as _ev
    ics = _ev.build_ics(
        uid="pin@illtowell.com", start_ts="2026-09-07T09:00:00",
        end_ts="2026-09-07T09:20:00", summary="Free 20 minute intro call",
        description="Free 20 minute intro call (phone). To cancel: "
                    "https://illtowell.com/book/cancel?slug=mary-boyd&"
                    "start=2026-09-07T09:00:00&token=abc",
        location="phone")
    assert ics == (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//illtowell//EVOX//EN\r\n"
        b"METHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:pin@illtowell.com\r\n"
        b"DTSTAMP:20260907T000000\r\nDTSTART:20260907T090000\r\n"
        b"DTEND:20260907T092000\r\nSUMMARY:Free 20 minute intro call\r\n"
        b"DESCRIPTION:Free 20 minute intro call (phone). To cancel: "
        b"https://illtowell.com/book/cancel?slug=mary-boyd&"
        b"start=2026-09-07T09:00:00&token=abc\r\nLOCATION:phone\r\n"
        b"ORGANIZER:mailto:rae@illtowell.com\r\nSTATUS:CONFIRMED\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n")


def test_build_ics_with_tz_name_emits_utc_with_a_z_suffix():
    """Same wall-clock input as the pinned byte-identity test above, but
    with tz_name given: DTSTART/DTEND must be the correct UTC conversion
    (America/Anchorage is UTC-8 in September, daylight time), not the naive
    local string with a bare Z appended, and DTSTART must end with Z."""
    from dashboard import evox as _ev
    from zoneinfo import ZoneInfo as _ZI
    from datetime import datetime as _dt, timezone as _tz
    import re as _re
    ics = _ev.build_ics(
        uid="pin@illtowell.com", start_ts="2026-09-07T09:00:00",
        end_ts="2026-09-07T09:20:00", summary="x", description="x",
        location="phone", tz_name="America/Anchorage")
    t = ics.decode()
    expected_start = _dt(2026, 9, 7, 9, 0, 0, tzinfo=_ZI("America/Anchorage")) \
        .astimezone(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    expected_end = _dt(2026, 9, 7, 9, 20, 0, tzinfo=_ZI("America/Anchorage")) \
        .astimezone(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    assert expected_start != "20260907T090000Z", \
        "test is meaningless if Anchorage has a zero UTC offset on this date"
    assert f"DTSTART:{expected_start}" in t
    assert f"DTEND:{expected_end}" in t
    dtstart_val = _re.search(r"DTSTART:(\S+)", t).group(1)
    assert dtstart_val.endswith("Z")


def test_a_cancel_token_releases_the_slot(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    token = r.get_json()["cancel_token"]

    c2 = public.post("/api/book/mary-boyd/cancel",
                     json={"start": slot["start"], "token": token})
    assert c2.status_code == 200
    with _open(logdb) as c:
        row = c.execute("SELECT status FROM evox_bookings").fetchone()
        assert row["status"] != "booked"
    again = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"]
    assert any(s["start"] == slot["start"] for s in again), \
        "a cancelled slot must become available again"


def test_a_forged_cancel_token_is_refused(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    r = public.post("/api/book/mary-boyd/cancel",
                    json={"start": slot["start"], "token": "0" * 32})
    assert r.status_code == 403
    with _open(logdb) as c:
        assert c.execute("SELECT status FROM evox_bookings").fetchone()["status"] == "booked"


def test_a_cancel_token_for_one_slot_does_not_cancel_another(public, logdb):
    """The token is scoped to (practitioner, slot, booking id). One
    booking's token must not be a skeleton key for the practitioner's whole
    day. token_a is read from booking A's own response (rather than
    recomputed via pb.cancel_token) because cancel_token now takes the
    booking's row id as a third argument, and the route -- not the test --
    is the thing that should decide what that id is."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slots = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"]
    a, b = slots[0], slots[1]
    token_a = None
    for s in (a, b):
        r = public.post("/api/book/mary-boyd", json={
            "session": "intro", "start": s["start"],
            "name": "A Client", "email": "client@example.com"})
        if s is a:
            token_a = r.get_json()["cancel_token"]
    r = public.post("/api/book/mary-boyd/cancel",
                    json={"start": b["start"], "token": token_a})
    assert r.status_code == 403


def test_a_stale_token_from_a_cancelled_and_rebooked_slot_is_refused(public, logdb):
    """A token is a pure function of (practitioner, slot, booking id). If it
    were only a function of (practitioner, slot) it would stay valid
    forever -- including after the slot is cancelled and rebooked by a
    different client, which would turn the FIRST client's saved
    confirmation email into a permanent cancel credential for the second
    client's appointment. Book A, cancel A, rebook the same slot as B: A's
    original token must no longer work."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r_a = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Client A", "email": "a@example.com"})
    token_a = r_a.get_json()["cancel_token"]
    c1 = public.post("/api/book/mary-boyd/cancel",
                     json={"start": slot["start"], "token": token_a})
    assert c1.status_code == 200

    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Client B", "email": "b@example.com"})

    stale = public.post("/api/book/mary-boyd/cancel",
                        json={"start": slot["start"], "token": token_a})
    assert stale.status_code == 403, \
        "A's old confirmation email must not be able to cancel B's appointment"
    with _open(logdb) as c:
        row = c.execute("SELECT client_name, status FROM evox_bookings "
                        "WHERE status='booked'").fetchone()
        assert row["client_name"] == "Client B" and row["status"] == "booked"


def test_the_rebooked_slots_own_token_still_works(public, logdb):
    """Continuing the same cancel/rebook sequence: B's own cancel_token
    (returned from B's own booking response) must still cancel B's own
    booking -- the id-binding refuses a STALE token, not every token."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r_a = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Client A", "email": "a@example.com"})
    public.post("/api/book/mary-boyd/cancel",
               json={"start": slot["start"], "token": r_a.get_json()["cancel_token"]})
    r_b = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "Client B", "email": "b@example.com"})
    token_b = r_b.get_json()["cancel_token"]

    c2 = public.post("/api/book/mary-boyd/cancel",
                     json={"start": slot["start"], "token": token_b})
    assert c2.status_code == 200
    with _open(logdb) as c:
        row = c.execute("SELECT status FROM evox_bookings WHERE client_name='Client B'").fetchone()
        assert row["status"] != "booked"


def test_a_get_request_cannot_cancel_a_booking(public, logdb):
    """Mail scanners and link-prefetchers issue GET on every URL in an
    email. The cancel API must refuse GET outright (405, Flask's own
    method-not-allowed) rather than treat it as a cancel -- the same
    reasoning as _confirm_post_page: a state change must wait for a human
    to submit a form/press a button, which only ever happens via POST."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    r = public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"],
        "name": "A Client", "email": "client@example.com"})
    token = r.get_json()["cancel_token"]

    g = public.get(f"/api/book/mary-boyd/cancel?start={slot['start']}&token={token}")
    assert g.status_code == 405
    with _open(logdb) as c:
        assert c.execute("SELECT status FROM evox_bookings").fetchone()["status"] == "booked"


def test_the_public_page_shows_the_button_only_when_configured(public, logdb, monkeypatch):
    """The Book link on the public practitioner page must reflect real config,
    not just that the booking feature exists -- 22 of 23 practitioners have
    never configured it, and an empty booking page is worse than no button."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("canonical", s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: {"slug": slug, "practitioner_name": "Mary Boyd",
                                          "practice_name": "", "bio": "", "photo_url": "",
                                          "logo_url": "", "services": [], "location": "",
                                          "accepting_clients": None, "featured_products": [],
                                          "catalog_url": "/e", "profit_disclosure": "d",
                                          "tagline": "", "how_i_work": ""})
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    assert "Book" not in public.get("/mary-boyd").get_data(as_text=True)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    assert "Book" in public.get("/mary-boyd").get_data(as_text=True)


def test_the_bookable_check_skips_resolution_when_nothing_is_configured(
        public, logdb, monkeypatch):
    """_render_practitioner_page used to call _pb.init_tables (a CREATE TABLE
    + commit -- a write transaction on every read) and
    _pb.resolve_practitioner_pid (a fresh unpooled Supabase connection) on
    EVERY view of EVERY practitioner page, to answer a question that is
    False for all 23 live pages today -- nobody has enabled booking yet. The
    fix runs one cheap local `SELECT 1 FROM practitioner_booking_config
    WHERE enabled=1 LIMIT 1` first and skips both entirely when it finds
    nothing. Proven here by making both explode if called at all -- the page
    must still render successfully, with no Book link, when there is
    nothing configured."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("canonical", s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: {"slug": slug, "practitioner_name": "Mary Boyd",
                                          "practice_name": "", "bio": "", "photo_url": "",
                                          "logo_url": "", "services": [], "location": "",
                                          "accepting_clients": None, "featured_products": [],
                                          "catalog_url": "/e", "profit_disclosure": "d",
                                          "tagline": "", "how_i_work": ""})
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    # Count calls rather than raising: _render_practitioner_page wraps this
    # whole block in a broad `except Exception`, so a raise here would be
    # silently swallowed and this test would pass whether or not the early
    # exit actually worked (verified: this WAS the first version of this
    # test, and it stayed green even with the early-exit code deleted).
    # Counting survives that catch-all.
    calls = {"init_tables": 0, "resolve_practitioner_pid": 0}
    real_init_tables = pb.init_tables
    real_resolve = pb.resolve_practitioner_pid

    def _counted_init_tables(*a, **k):
        calls["init_tables"] += 1
        return real_init_tables(*a, **k)

    def _counted_resolve(*a, **k):
        calls["resolve_practitioner_pid"] += 1
        return real_resolve(*a, **k)
    monkeypatch.setattr(pb, "init_tables", _counted_init_tables)
    monkeypatch.setattr(pb, "resolve_practitioner_pid", _counted_resolve)

    r = public.get("/mary-boyd")
    assert r.status_code == 200
    assert "Book" not in r.get_data(as_text=True)
    assert calls["init_tables"] == 0, (
        "init_tables (a CREATE TABLE + commit) ran even though the cheap "
        "local check found nothing configured")
    assert calls["resolve_practitioner_pid"] == 0, (
        "resolve_practitioner_pid (a fresh Supabase connection) ran even "
        "though the cheap local check found nothing configured")


# --- Task 7: the visitor-facing booking page -------------------------------
# Tasks 1-6 built config storage, timezone-correct slot maths, a practitioner
# config form, the public booking API, the confirmation email/cancel flow,
# and the Book link on the public page -- but nothing a visitor can actually
# open. This is the missing GET /book/<slug> page.


class _AlwaysPublicClient:
    """A plain wrapper around app.test_client() whose .get() enables the
    public surface for the duration of that one call only, via a context
    manager -- not a leaked `mock.patch(...).start()` with no matching
    stop(), which would bleed into whichever test runs next in this module.

    Used only by tests that take no monkeypatch fixture (per the brief) and
    so cannot patch _public_surface_enabled the normal way."""

    def __init__(self, client):
        self._c = client

    def get(self, *a, **kw):
        with mock.patch.object(appmod, "_public_surface_enabled", lambda: True):
            return self._c.get(*a, **kw)


def _client():
    return _AlwaysPublicClient(appmod.app.test_client())


def test_the_booking_page_serves_for_a_configured_practitioner(public, logdb):
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    r = public.get("/book/mary-boyd")
    assert r.status_code == 200
    assert "text/html" in r.headers["Content-Type"]


def test_the_booking_page_is_noindex():
    """A booking form has nothing to offer a search engine, and indexing one
    practitioner's availability page is a privacy surprise."""
    r = _client().get("/book/mary-boyd")
    assert r.headers.get("X-Robots-Tag") == "noindex"


def test_the_booking_page_404s_when_the_public_surface_is_off(monkeypatch, logdb):
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: False)
    assert appmod.app.test_client().get("/book/mary-boyd").status_code == 404


def test_the_book_link_on_the_public_page_points_at_a_real_route(public, logdb, monkeypatch):
    """The defect this task exists to fix: Task 6 emitted href="/book/<slug>"
    and nothing served it. Assert the link target actually resolves rather
    than trusting that it does.

    /<slug> is host-gated to the portal host (see app.py's practitioner_site
    docstring) and PORTAL_BASE_URL is unset in this environment, so
    _on_portal_host() and practitioner_slugs.resolve() are stood up the same
    way test_the_public_page_shows_the_button_only_when_configured does it,
    and affiliate_signups is seeded so the real build_practitioner_storefront
    (unmocked, unlike that other test) has a row to find.
    """
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("canonical", s))
    _seed_slug(logdb)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    import re
    page = public.get("/mary-boyd").get_data(as_text=True)
    m = re.search(r'href="(/book/[^"]+)"', page)
    assert m, "no Book link on a bookable practitioner's page"
    assert public.get(m.group(1)).status_code == 200, \
        f"the Book link points at {m.group(1)}, which does not serve"


def test_the_page_posts_the_practitioner_local_start_not_the_visitor_string():
    """The API's `start` field is the value to post back; `visitor` is for
    display only. Posting the visitor string would be rejected as
    slot_unavailable, or worse, silently book a different instant."""
    import pathlib
    js = (pathlib.Path(appmod.STATIC) / "book.html").read_text()
    assert "s.start" in js or "slot.start" in js
    assert "rendered_timezone" in js, \
        "the page must label times from the zone the API actually used"


def test_the_source_has_a_rate_limited_branch():
    """A source check only -- it passes whether or not the branch is ever
    reached, which is exactly why it is paired with the Node-extraction
    test right below. Kept as a cheap sanity check that survives even on a
    host with no `node` binary."""
    import pathlib
    js = (pathlib.Path(appmod.STATIC) / "book.html").read_text()
    assert "rate_limited" in js


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_a_rate_limited_booking_is_told_to_wait_not_to_retry():
    """The velocity guard added in the previous wave returns
    {"error": "rate_limited"} with a 429 (app.py's _velocity_guard). Before
    this fix, book.html's error ladder had no branch for that code, so it
    fell to the generic "Something went wrong. Please try again." -- inviting
    an immediate retry that keeps failing for up to a minute, or up to a day
    if the daily cap tripped.

    A plain grep of the source (test_the_source_has_a_rate_limited_branch,
    above) would pass whether or not any code path actually reaches the
    branch. This test instead uses the same extraction-and-execute-under-Node
    technique as test_fmt_time_renders_in_the_rendered_timezone_not_the_browsers_own:
    it pulls the real, named, pure bookErrorMessage(err) function out of
    book.html by balanced-brace slicing and calls it directly, rather than
    driving the full click handler (which touches document.getElementById,
    fetch, and DOM state that would need a jsdom-equivalent stub to exercise
    meaningfully). bookErrorMessage is where the error-code -> copy mapping
    actually lives; the two side-effecting codes (slot_taken/slot_unavailable)
    stay in the caller and are not covered by this function on purpose.
    """
    import pathlib
    js = r'''
      const fs = require('fs');
      const src = fs.readFileSync('static/book.html', 'utf8');
      const marker = 'function bookErrorMessage(';
      const idx = src.indexOf(marker);
      if (idx < 0) { console.error('bookErrorMessage not found in book.html'); process.exit(2); }
      const braceStart = src.indexOf('{', idx);
      let depth = 0, end = -1;
      for (let j = braceStart; j < src.length; j++) {
        if (src[j] === '{') depth++;
        else if (src[j] === '}') { depth--; if (depth === 0) { end = j; break; } }
      }
      if (end < 0) { console.error('unbalanced braces extracting bookErrorMessage'); process.exit(2); }
      const fnSrc = src.slice(idx, end + 1);
      eval(fnSrc);
      if (typeof bookErrorMessage !== 'function') {
        console.error('extraction did not produce a callable bookErrorMessage'); process.exit(2);
      }

      const got = bookErrorMessage('rate_limited');
      if (typeof got !== 'string' || !got.length) {
        console.error('rate_limited produced no message: ' + JSON.stringify(got));
        process.exit(1);
      }
      const fallback = bookErrorMessage('some_unknown_future_code');
      if (got === fallback) {
        console.error('rate_limited is not branched -- it falls through to the generic message: ' +
                       JSON.stringify(got));
        process.exit(1);
      }
      const low = got.toLowerCase();
      if (low.indexOf('try again') !== -1 || /\btry\b/.test(low)) {
        console.error('copy invites an immediate retry, which is the bug this fix closes: ' +
                       JSON.stringify(got));
        process.exit(1);
      }
      if (!/wait/.test(low)) {
        console.error('copy does not tell the visitor to wait: ' + JSON.stringify(got));
        process.exit(1);
      }
      console.log('ok');
    '''
    out = subprocess.run(["node", "-e", js], cwd=str(pathlib.Path(appmod.STATIC).parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"stdout={out.stdout!r} stderr={out.stderr!r}"


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_fmt_time_renders_in_the_rendered_timezone_not_the_browsers_own():
    """Round 1 fix: fmtTime() called toLocaleString(undefined, {...}) with no
    `timeZone` option, so the DIGITS came out in whatever zone the host
    environment defaulted to, while the "Times below are shown in ..." note
    above the list was correctly built from rendered_timezone -- the two only
    agreed when the server happened to pick the visitor's own zone. On the
    fallback path rendered_timezone exists for (the browser reports an
    unusable zone, the API falls back to the practitioner's own zone and
    says so), the label and the times disagreed -- exactly the failure this
    field exists to prevent.

    A substring check for "rendered_timezone" appearing anywhere in the file
    (the previous version of this guard) passes whether or not anything
    actually uses it, so it caught nothing. This test instead extracts the
    real fmtTime() function out of book.html by balanced-brace slicing and
    executes it in Node with a fixed instant and two different IANA zones
    18+ hours apart (the brief's own America/Anchorage / Pacific/Auckland
    example for 2026-09-07 17:00 UTC). Each expected value is computed with
    the *same* toLocaleString options fmtTime uses, so this does not depend
    on any hardcoded locale-formatted string or the test host's own default
    zone -- it only passes if fmtTime actually threads `tz` into
    toLocaleString's `timeZone` option.
    """
    import pathlib
    html = (pathlib.Path(appmod.STATIC) / "book.html").read_text()
    js = r'''
      const fs = require('fs');
      const src = fs.readFileSync('static/book.html', 'utf8');
      const marker = 'function fmtTime(';
      const idx = src.indexOf(marker);
      if (idx < 0) { console.error('fmtTime not found in book.html'); process.exit(2); }
      const braceStart = src.indexOf('{', idx);
      let depth = 0, end = -1;
      for (let j = braceStart; j < src.length; j++) {
        if (src[j] === '{') depth++;
        else if (src[j] === '}') { depth--; if (depth === 0) { end = j; break; } }
      }
      if (end < 0) { console.error('unbalanced braces extracting fmtTime'); process.exit(2); }
      const fnSrc = src.slice(idx, end + 1);
      eval(fnSrc);
      if (typeof fmtTime !== 'function') {
        console.error('extraction did not produce a callable fmtTime'); process.exit(2);
      }

      // The same UTC instant the brief's own example uses: 09:00 in
      // America/Anchorage (UTC-8 in September) == 05:00 the next day in
      // Pacific/Auckland (UTC+12, pre-NZDT in early September).
      const iso = '2026-09-07T17:00:00Z';
      const opts = {weekday: 'short', month: 'short', day: 'numeric',
                    hour: 'numeric', minute: '2-digit'};
      const d = new Date(iso);
      const expectA = d.toLocaleString(undefined, Object.assign({}, opts, {timeZone: 'America/Anchorage'}));
      const expectB = d.toLocaleString(undefined, Object.assign({}, opts, {timeZone: 'Pacific/Auckland'}));
      const gotA = fmtTime(iso, 'America/Anchorage');
      const gotB = fmtTime(iso, 'Pacific/Auckland');

      if (gotA !== expectA) {
        console.error('Anchorage mismatch: got ' + JSON.stringify(gotA) +
                       ' expected ' + JSON.stringify(expectA));
        process.exit(1);
      }
      if (gotB !== expectB) {
        console.error('Auckland mismatch: got ' + JSON.stringify(gotB) +
                       ' expected ' + JSON.stringify(expectB));
        process.exit(1);
      }
      if (gotA === gotB) {
        console.error('same output for two zones 20 hours apart -- tz is being ignored: ' +
                       JSON.stringify(gotA));
        process.exit(1);
      }
      console.log('ok');
    '''
    out = subprocess.run(["node", "-e", js], cwd=str(pathlib.Path(appmod.STATIC).parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"stdout={out.stdout!r} stderr={out.stderr!r}"
    assert "timeZone" in html, \
        "fmtTime's toLocaleString call must pass a timeZone option somewhere"


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_cancel_page_fmt_time_renders_in_the_passed_timezone_not_the_browsers_own():
    """The bug this fix exists to close: book-cancel.html's `when` display
    used to do `new Date(start)` on the naive PRACTITIONER-local string with
    no timeZone pinned, so an Auckland client who booked "Tue 8 Sep, 5:00 AM"
    (their own zone) could land on a page reading "Monday, September 7" --
    the wrong DAY, not just a shifted hour -- and reasonably concludes it's
    the wrong appointment, which is exactly the no-show the cancel link
    exists to prevent.

    Same extraction-and-execute-under-Node technique as
    test_fmt_time_renders_in_the_rendered_timezone_not_the_browsers_own
    (book.html), applied to book-cancel.html's own fmtTime -- not a grep of
    the source, which would pass whether or not anything actually uses the
    zone. Uses the SAME fixed UTC instant and the same two zones 18+ hours
    apart as that test, so a correct implementation must clear it the same
    way: two different renderings, each matching an independently-computed
    toLocaleString call with the SAME options fmtTime uses.
    """
    import pathlib
    html = (pathlib.Path(appmod.STATIC) / "book-cancel.html").read_text()
    js = r'''
      const fs = require('fs');
      const src = fs.readFileSync('static/book-cancel.html', 'utf8');
      const marker = 'function fmtTime(';
      const idx = src.indexOf(marker);
      if (idx < 0) { console.error('fmtTime not found in book-cancel.html'); process.exit(2); }
      const braceStart = src.indexOf('{', idx);
      let depth = 0, end = -1;
      for (let j = braceStart; j < src.length; j++) {
        if (src[j] === '{') depth++;
        else if (src[j] === '}') { depth--; if (depth === 0) { end = j; break; } }
      }
      if (end < 0) { console.error('unbalanced braces extracting fmtTime'); process.exit(2); }
      const fnSrc = src.slice(idx, end + 1);
      eval(fnSrc);
      if (typeof fmtTime !== 'function') {
        console.error('extraction did not produce a callable fmtTime'); process.exit(2);
      }

      const iso = '2026-09-07T17:00:00Z';
      const opts = {weekday: 'long', month: 'long', day: 'numeric',
                    hour: 'numeric', minute: '2-digit'};
      const d = new Date(iso);
      const expectA = d.toLocaleString(undefined, Object.assign({}, opts, {timeZone: 'America/Anchorage'}));
      const expectB = d.toLocaleString(undefined, Object.assign({}, opts, {timeZone: 'Pacific/Auckland'}));
      const gotA = fmtTime(iso, 'America/Anchorage');
      const gotB = fmtTime(iso, 'Pacific/Auckland');

      if (gotA !== expectA) {
        console.error('Anchorage mismatch: got ' + JSON.stringify(gotA) +
                       ' expected ' + JSON.stringify(expectA));
        process.exit(1);
      }
      if (gotB !== expectB) {
        console.error('Auckland mismatch: got ' + JSON.stringify(gotB) +
                       ' expected ' + JSON.stringify(expectB));
        process.exit(1);
      }
      if (gotA === gotB) {
        console.error('same output for two zones 20 hours apart -- tz is being ignored: ' +
                       JSON.stringify(gotA));
        process.exit(1);
      }
      console.log('ok');
    '''
    out = subprocess.run(["node", "-e", js], cwd=str(pathlib.Path(appmod.STATIC).parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"stdout={out.stdout!r} stderr={out.stderr!r}"
    assert "timeZone" in html, \
        "fmtTime's toLocaleString call must pass a timeZone option somewhere"


def test_the_cancel_url_carries_the_visitor_instant_and_zone(public, logdb, monkeypatch):
    """The `when`/`tz` params book-cancel.html's fmtTime depends on must
    actually reach the emailed cancel link. `when` must be a real,
    offset-bearing instant (not the naive practitioner-local `start`, which
    the browser would parse in its OWN zone) and must land on the same wall
    clock reading the client already saw in the confirmation body/ics."""
    sent = []
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subject, html_body, text_body, ics_bytes:
                        sent.append(text_body))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)  # CFG["timezone"] == "America/Anchorage"
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    body = sent[0]
    import re as _re
    m = _re.search(r"/book/cancel\?[^\s\"]+", body)
    assert m, "no cancel link in the confirmation body"
    cancel_link = m.group(0)
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(cancel_link.replace("/book/cancel?", "?")).query)
    assert qs.get("when"), "cancel link carries no `when` instant for the cancel page to render"
    assert qs.get("tz") == ["Pacific/Auckland"], \
        "cancel link must carry the same rendered zone the client already saw"
    # `when` must be the exact value to_visitor_tz computed for this booking
    # -- the same instant/zone already shown in the confirmation text.
    expected_when = pb.to_visitor_tz(slot["start"], CFG["timezone"], "Pacific/Auckland")
    assert qs["when"][0] == expected_when
    # `start` must remain the naive practitioner-local value -- the cancel
    # API matches/verifies the token against it unchanged.
    assert qs.get("start") == [slot["start"]]


# --- Task 2: practitioner phone lookup + GHL SMS sender ---------------------

def test_practitioner_phone_is_empty_when_unavailable(monkeypatch):
    """Same contract as practitioner_email_by_id: a Supabase failure returns
    "", never an exception, because the caller is mid-notification on a
    booking that is already committed."""
    from dashboard import practitioner_portal as _pp
    import db_supabase

    def boom():
        raise RuntimeError("supabase is down")
    monkeypatch.setattr(db_supabase, "supabase_cursor", boom)
    assert _pp.practitioner_phone_by_id("pid-x") == ""


def test_sms_is_skipped_not_raised_when_ghl_is_unconfigured(monkeypatch):
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: False)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    # Specific reason, not just "truthy skip" -- that alone can't distinguish
    # this branch from the pytest short-circuit branch below it.
    assert out.get("skipped") == "ghl not configured"
    assert "id" not in out


def test_sms_is_skipped_when_the_contact_lookup_fails(monkeypatch):
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: True)

    def boom(email, name="", phone=""):
        raise RuntimeError("no contact")
    monkeypatch.setattr(_g, "_upsert_contact", boom)
    # send_sms_via_ghl short-circuits to {"skipped": "pytest"} before it ever
    # reaches _upsert_contact, same as every other test in this file -- so
    # without this delenv, `boom` above never fires and the try/except this
    # test claims to cover has zero coverage. Deleting the env var is safe
    # ONLY because _upsert_contact is mocked to `boom` right above: real
    # execution now reaches the (fake) contact lookup but nothing past it.
    # Removing that mock while this delenv is in place would make this test
    # write to Glen's live CRM.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    assert "contact lookup failed" in out.get("skipped", "")


def test_the_sms_payload_is_type_sms_not_email():
    """The same GHL endpoint sends both. Sending type Email here would deliver
    a subject-less email instead of a text and look like success.

    Tested through a PURE payload builder rather than through
    send_sms_via_ghl, because that function short-circuits under pytest before
    it ever posts -- see the next test. A payload builder with no I/O is the
    only seam where this shape can actually be asserted."""
    from dashboard import ghl_email as _g
    body = _g._sms_payload("c-1", "New booking from A Client")
    assert body["type"] == "SMS"
    assert body["contactId"] == "c-1"
    assert "New booking" in body["message"]
    assert "subject" not in body


def test_send_sms_never_touches_the_live_crm_under_pytest(monkeypatch):
    """_upsert_contact is a LIVE CRM WRITE. Its own comment says a new caller
    must carry the pytest guard itself, so assert ours does -- if this test
    ever goes red, a test run is writing to the real CRM."""
    from dashboard import ghl_email as _g
    monkeypatch.setattr(_g, "is_configured", lambda: True)

    def must_not_run(*a, **kw):
        raise AssertionError("_upsert_contact reached under pytest")
    monkeypatch.setattr(_g, "_upsert_contact", must_not_run)
    out = _g.send_sms_via_ghl("her@example.com", "New booking")
    assert out.get("skipped") == "pytest"


# --- Task 3: fan the notification out across her chosen methods -------------

def _spy_sends(monkeypatch):
    sends = {"email": [], "sms": []}
    monkeypatch.setattr(appmod, "send_evox_email",
                        lambda to, name, subj, html, text, ics: sends["email"].append(
                            {"to": to, "subj": subj, "ics": ics}))
    from dashboard import ghl_email as _g
    monkeypatch.setattr(appmod, "_send_sms_via_ghl",
                        lambda to, msg, phone="": sends["sms"].append(
                            {"to": to, "msg": msg, "phone": phone}))
    return sends


def test_email_only_is_the_default_behaviour(public, logdb, monkeypatch):
    """Regression guard on the default path only. This asserts what the OLD
    single-email code already did (one email, no ICS) and would pass even if
    `methods` branching were deleted entirely -- it is not proof the fan-out
    reads notify_methods. test_text_sends_an_sms_with_her_own_timezone,
    test_phone_only_sends_her_nothing, and
    test_one_failing_method_does_not_stop_the_others are the ones that
    actually require the branching to exist."""
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)          # no notify_methods -> ["email"]
    _book_one(public)
    to_her = [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert len(to_her) == 1
    assert to_her[0]["ics"] == b"", "no calendar method chosen, so no invite"
    assert sends["sms"] == []


def test_calendar_attaches_the_invite_to_her_notification(public, logdb, monkeypatch):
    """Regression guard on the default-plus-calendar path only. This asserts
    what the OLD single-email code's output would look like if it happened to
    carry an ICS -- it does not by itself prove `methods` branching decides
    whether the ICS is attached (a version that always attached the invite
    would also pass this one). test_email_only_is_the_default_behaviour's
    `ics == b""` case is what actually shows the attach decision is
    conditional on `calendar` being chosen."""
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["email", "calendar"]))
    _book_one(public)
    to_her = [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert to_her and to_her[0]["ics"].startswith(b"BEGIN:VCALENDAR")


def test_text_sends_an_sms_with_her_own_timezone(public, logdb, monkeypatch):
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text"]))
    _book_one(public)
    assert len(sends["sms"]) == 1
    assert "A Client" in sends["sms"][0]["msg"]
    assert not [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL], \
        "she chose text only; she should not also get an email"


def test_phone_only_sends_her_nothing(public, logdb, monkeypatch):
    """Deliberate. 'Phone' means the client calls her, so there is nothing to
    send. Her number reaching the client is Task 4's job."""
    sends = _spy_sends(monkeypatch)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["phone"]))
    _book_one(public)
    assert not [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]
    assert sends["sms"] == []


def test_a_failing_sms_does_not_fail_the_booking(public, logdb, monkeypatch):
    def boom(to, msg, phone=""):
        raise RuntimeError("ghl is down")
    monkeypatch.setattr(appmod, "_send_sms_via_ghl", boom)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text"]))
    r = _book_one(public)
    assert r.status_code == 200
    with _open(logdb) as c:
        assert c.execute("SELECT COUNT(*) c FROM evox_bookings "
                         "WHERE status='booked'").fetchone()["c"] == 1


def test_one_failing_method_does_not_stop_the_others(public, logdb, monkeypatch):
    """She chose text and email. GHL is down. She must still get the email --
    a fan-out that aborts on the first failure is worse than no fan-out,
    because it silently drops the channel that would have worked.

    NOTE: this direction cannot detect a missing per-method try/except. The
    implementation runs the email/calendar branch before the text branch, so
    by the time the unwrapped SMS call would raise, the email has already
    been sent -- this assertion passes whether or not the SMS call is
    individually wrapped. It is a real regression guard (the email must
    still go out when GHL is down) but it does not prove per-method
    isolation. test_a_failing_email_does_not_stop_the_text is the one that
    actually exercises that: the direction where the FIRST branch fails and
    the second must still run.
    """
    sends = _spy_sends(monkeypatch)

    def boom(to, msg, phone=""):
        raise RuntimeError("ghl is down")
    monkeypatch.setattr(appmod, "_send_sms_via_ghl", boom)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["text", "email"]))
    _book_one(public)
    assert [s for s in sends["email"] if s["to"] == PRACTITIONER_EMAIL]


def test_a_failing_email_does_not_stop_the_text(public, logdb, monkeypatch):
    """The direction that actually carries information about per-method
    isolation. The email/calendar branch runs FIRST in the implementation,
    so this is the only ordering where an unwrapped failure could reach the
    outer catch-all before the second branch ever runs: if the email-side
    try/except were removed, send_evox_email raising here would propagate to
    the block-level except and the text branch would never execute, silently
    dropping the channel that would have worked. She chose email and text;
    her email provider is down; she must still get the text."""
    sends = _spy_sends(monkeypatch)

    def boom(to, name, subj, html, text, ics):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(appmod, "send_evox_email", boom)
    with _open(logdb) as c:
        pb.set_config(c, PID, dict(CFG, notify_methods=["email", "text"]))
    _book_one(public)
    assert sends["sms"], "text should still have been attempted even though email failed"
