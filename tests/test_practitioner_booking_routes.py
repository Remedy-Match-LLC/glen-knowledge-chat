"""Routes for booking configuration and public booking.

Assertions are on raw response bytes and JSON, never a parsed DOM.
"""
import contextlib
import os
import sqlite3
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
        c.execute("""CREATE TABLE IF NOT EXISTS affiliate_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
            slug TEXT, status TEXT)""")
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
    sent = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.append(
                            {"to": to, "subject": subject, "body": body}))
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


def test_the_confirmation_states_the_time_in_the_visitor_timezone(public, logdb, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **kw: sent.append(body))
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slot = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"][0]
    public.post("/api/book/mary-boyd", json={
        "session": "intro", "start": slot["start"], "tz": "Pacific/Auckland",
        "name": "A Client", "email": "client@example.com"})
    assert "Pacific/Auckland" in sent[0] or "NZ" in sent[0], \
        "a client cannot act on a time in a zone they do not live in"


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
    """The token is scoped to (practitioner, slot). One booking's token must
    not be a skeleton key for the practitioner's whole day."""
    with _open(logdb) as c:
        pb.set_config(c, PID, CFG)
    slots = public.get("/api/book/mary-boyd/slots?session=intro").get_json()["slots"]
    a, b = slots[0], slots[1]
    for s in (a, b):
        public.post("/api/book/mary-boyd", json={
            "session": "intro", "start": s["start"],
            "name": "A Client", "email": "client@example.com"})
    token_a = pb.cancel_token(PID, a["start"])
    r = public.post("/api/book/mary-boyd/cancel",
                    json={"start": b["start"], "token": token_a})
    assert r.status_code == 403


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
