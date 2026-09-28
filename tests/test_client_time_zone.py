"""Each client's time zone, and times shown in it with Hawaii time in brackets.

Glen, 2026-09-28 ("yes" to the design): store each client's zone (the browser's on first
visit, changeable in the portal), show every portal and email time in the client's zone with
Hawaii time in brackets, keep the welcome call to 3 times a day but hold the choice steady per
client and day, and send calendar invites with the real zone."""
import sqlite3
from unittest import mock

import pytest

import app as appmod
from dashboard import client_time as ct
from dashboard import onboarding as ob

LA, HNL = "America/Los_Angeles", "Pacific/Honolulu"
RUN = __import__("uuid").uuid4().hex[:8]     # the test database is shared across runs


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _seed_member(email):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        from dashboard import evox as _ev, client_portal as _cp
        _ev.init_evox_tables(cx)
        _cp.init_client_portal_table(cx)
        appmod._init_people_table()
        cx.execute("INSERT OR IGNORE INTO people (email, created_at, updated_at) VALUES (?,'','')",
                   (email,))
        token = _ev.ensure_portal_token(cx, email, "")
        cx.commit()
    return token


def _zone(email):
    with sqlite3.connect(appmod.LOG_DB) as cx:
        return ct.get_zone(cx, email)


# ── Formatting ───────────────────────────────────────────────────────────────
def test_a_time_is_shown_in_the_clients_zone_with_hawaii_in_brackets():
    assert ct.describe("2026-09-30T11:00", LA) == "Wed 30 Sep, 2:00 pm (11:00 am Hawaii)"


def test_a_hawaii_client_or_no_zone_reads_hawaii_time():
    assert ct.describe("2026-09-30T11:00", HNL) == "Wed 30 Sep, 11:00 am Hawaii time"
    assert ct.describe("2026-09-30T11:00", "") == "Wed 30 Sep, 11:00 am Hawaii time"
    assert ct.describe("2026-09-30T11:00", "Not/AZone") == "Wed 30 Sep, 11:00 am Hawaii time"


def test_a_different_day_is_named():
    # 8:00 pm Hawaii on the 30th is the 1st of October in London.
    assert ct.describe("2026-09-30T20:00", "Europe/London") == "Thu 1 Oct, 7:00 am (8:00 pm Wed 30 Sep Hawaii)"


# ── Storing the zone ─────────────────────────────────────────────────────────
def test_the_browser_fills_an_empty_zone_and_never_overwrites_a_choice():
    tok = _seed_member(f"tz1-{RUN}@x.com")
    c = _client()
    assert c.post(f"/api/portal/time-zone/browser?token={tok}", json={"tz": LA}).status_code == 200
    assert _zone(f"tz1-{RUN}@x.com") == LA
    c.post(f"/api/portal/time-zone?token={tok}", json={"tz": "Europe/London", "source": "chosen"})
    assert _zone(f"tz1-{RUN}@x.com") == "Europe/London"
    c.post(f"/api/portal/time-zone/browser?token={tok}", json={"tz": LA})
    assert _zone(f"tz1-{RUN}@x.com") == "Europe/London"          # a choice wins over the browser
    assert c.get(f"/api/portal/time-zone?token={tok}").get_json()["tz"] == "Europe/London"


def test_an_unknown_zone_is_refused():
    tok = _seed_member(f"tz2-{RUN}@x.com")
    r = _client().post(f"/api/portal/time-zone?token={tok}", json={"tz": "Mars/Base", "source": "chosen"})
    assert r.status_code == 400 and _zone(f"tz2-{RUN}@x.com") == ""


def test_time_zone_needs_a_portal():
    assert _client().post("/api/portal/time-zone?token=nope", json={"tz": LA}).status_code == 404


# ── Welcome call: 3 a day, steady per client ─────────────────────────────────
DAY = [f"2026-09-30T{h:02d}:{m:02d}:00" for h in range(9, 16) for m in (0, 15, 30, 45)]


def test_the_three_times_hold_steady_for_a_client_and_day():
    a = ob.daily_slot_sample(DAY, seed="a@x.com")
    assert len(a) == 3 and a == ob.daily_slot_sample(DAY, seed="a@x.com")
    others = {tuple(ob.daily_slot_sample(DAY, seed=f"{i}@x.com")) for i in range(8)}
    assert len(others) > 1                                   # clients see different times


# ── Booking: reminders, emails and the invite use the zone ───────────────────
def test_a_booking_carries_the_zone_and_the_emails_show_it():
    tok = _seed_member(f"tz3-{RUN}@x.com")
    c = _client()
    c.post(f"/api/portal/time-zone?token={tok}", json={"tz": LA, "source": "chosen"})
    with mock.patch.object(appmod, "_is_paid_member", return_value=True), \
         mock.patch.object(appmod, "send_evox_email") as send:
        slot = c.get(f"/api/onboarding/availability?token={tok}").get_json()["slots"][0]
        assert c.post(f"/api/onboarding/book?token={tok}", json={"start_ts": slot}).status_code == 200
    with sqlite3.connect(appmod.LOG_DB) as cx:
        tz = cx.execute("SELECT visitor_tz FROM evox_bookings WHERE email=? AND session_type='onboarding'",
                        (f"tz3-{RUN}@x.com",)).fetchone()[0]
    assert tz == LA                                          # reminders convert with it
    want = ct.describe(slot, LA)
    to_client = [c_ for c_ in send.call_args_list if c_.args[0] == f"tz3-{RUN}@x.com"][0]
    assert want in to_client.args[3] and "HST" not in to_client.args[3]
    ics = to_client.args[5].decode() if isinstance(to_client.args[5], bytes) else str(to_client.args[5])
    assert "DTSTART:" in ics and "Z" in ics.split("DTSTART:")[1].splitlines()[0]


# ── The portal shows the same text the emails do ─────────────────────────────
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = r"""
const assert = require('assert');
let seg = "tok";
BLOCK
const cases = CASES;
for (const [ts, tz, want] of cases) {
  assert.strictEqual(hawaiiTime(ts, tz), want, ts + ' ' + tz);
}
CLIENT_TZ = "America/Los_Angeles";
assert.strictEqual(hawaiiTime("2026-09-30T11:00:00"), cases[0][2]);   // default: the client's zone
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_portal_formats_times_like_the_emails(tmp_path):
    import json as _json
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    a, b = page.find("// BEGIN client time zone"), page.find("// END client time zone")
    assert a != -1 and b > a
    cases = [[ts, tz, ct.describe(ts, tz)] for ts, tz in (
        ("2026-09-30T11:00:00", LA), ("2026-09-30T11:00:00", HNL), ("2026-09-30T11:00:00", ""),
        ("2026-09-30T20:00:00", "Europe/London"), ("2026-12-01T09:15:00", "America/New_York"),
        ("2026-09-30T11:00:00", "Not/AZone"))]
    js = tmp_path / "t.js"
    js.write_text(JS.replace("BLOCK", page[a:b]).replace("CASES", _json.dumps(cases)))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout


def test_no_portal_time_is_labelled_hst():
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    assert " HST</b>" not in page and '+" HST"' not in page


def test_the_consult_confirmation_uses_the_zone_too():
    b = {"start_ts": "2026-09-30T11:00:00", "end_ts": "2026-09-30T11:30:00", "ics_uid": "u1",
         "portal_url": "https://x/portal/t", "client_tz": LA}
    with mock.patch.object(appmod, "send_evox_email") as send:
        appmod._consult_send_confirmations("c@x.com", b)
    client = [c_ for c_ in send.call_args_list if c_.args[0] == "c@x.com"][0]
    assert "Wed 30 Sep, 2:00 pm (11:00 am Hawaii)" in client.args[3]
    assert "HST" not in client.args[3]
    glen = [c_ for c_ in send.call_args_list if c_.args[0] != "c@x.com"][0]
    assert "11:00 am Hawaii time" in glen.args[3] and "for the client that is" in glen.args[3]



# ── Review rounds 1 and 2 ────────────────────────────────────────────────────
def test_a_staff_view_never_saves_its_own_zone_onto_the_client(monkeypatch):
    tok = _seed_member(f"tz4-{RUN}@x.com")
    monkeypatch.setattr(appmod, "_staff_touch", lambda: True)
    r = _client().post(f"/api/portal/time-zone/browser?token={tok}", json={"tz": HNL})
    assert r.status_code == 204 and _zone(f"tz4-{RUN}@x.com") == ""


def test_the_browser_report_is_a_background_write():
    from dashboard import staff_guard as sg
    assert sg.classify("POST", "/api/portal/time-zone/browser") == "background"
    assert sg.classify("POST", "/api/portal/time-zone") == "guard"     # a choice is guarded


def test_a_browser_report_never_replaces_a_choice_even_in_a_race(monkeypatch):
    """Round 2: the empty-check and the write were separate; another worker's choice could
    land between them."""
    email = f"tz5-{RUN}@x.com"
    _seed_member(email)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ct.set_zone(cx, email, "Europe/London", source="chosen")
        cx.commit()
        monkeypatch.setattr(ct, "get_zone", lambda cx_, e: "")      # the stale read
        ct.set_zone(cx, email, LA, source="browser")
        cx.commit()
    monkeypatch.undo()
    assert _zone(email) == "Europe/London"


def test_saving_a_zone_never_creates_a_person():
    email = f"ghost-{RUN}@x.com"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        appmod._init_people_table()
        assert ct.set_zone(cx, email, LA, source="chosen") == ""
        cx.commit()
        assert cx.execute("SELECT COUNT(*) FROM people WHERE email=?", (email,)).fetchone()[0] == 0


def test_a_merged_address_saves_onto_the_surviving_person():
    from dashboard import person_aliases as pa
    alias, survivor = f"old-{RUN}@x.com", f"new-{RUN}@x.com"
    _seed_member(survivor)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        pa.init_tables(cx)
        cx.execute("INSERT INTO email_aliases (alias_email, canonical_email, merge_id, created_at) "
                   "VALUES (?,?,1,'t')", (alias, survivor))
        ct.set_zone(cx, alias, LA, source="chosen")
        cx.commit()
        assert ct.get_zone(cx, alias) == LA
    assert _zone(survivor) == LA


def test_booked_times_leave_the_other_shown_times_in_place():
    """Round 2: sampling a changed list reshuffled every time when one was booked."""
    shown = ob.daily_slot_sample(DAY, seed="a@x.com")
    unshown = [x for x in DAY if x not in shown]
    assert ob.daily_slot_sample([x for x in DAY if x != unshown[0]], seed="a@x.com") == shown
    after = ob.daily_slot_sample([x for x in DAY if x != shown[0]], seed="a@x.com")
    assert shown[1] in after and shown[2] in after and len(after) == 3


def test_a_first_booking_carries_the_zone_the_portal_showed():
    """Round 2: a booking could reach the server before the browser's zone was saved."""
    tok = _seed_member(f"tz6-{RUN}@x.com")
    c = _client()
    with mock.patch.object(appmod, "_is_paid_member", return_value=True), \
         mock.patch.object(appmod, "send_evox_email"):
        slot = c.get(f"/api/onboarding/availability?token={tok}").get_json()["slots"][0]
        c.post(f"/api/onboarding/book?token={tok}", json={"start_ts": slot, "tz": LA})
    with sqlite3.connect(appmod.LOG_DB) as cx:
        tz = cx.execute("SELECT visitor_tz FROM evox_bookings WHERE email=? AND session_type='onboarding'",
                        (f"tz6-{RUN}@x.com",)).fetchone()[0]
    assert tz == LA and _zone(f"tz6-{RUN}@x.com") == LA


def test_the_invite_holds_the_exact_instant_and_the_reminder_converts():
    from datetime import datetime, timedelta
    from dashboard import evox as ev
    ics = ev.build_ics(uid="u", start_ts="2026-09-30T11:00:00", end_ts="2026-09-30T11:15:00",
                       summary="s", description="d", location="Phone", tz_name=ct.HAWAII)
    ics = ics.decode() if isinstance(ics, bytes) else ics
    assert "DTSTART:20260930T210000Z" in ics                  # 11:00 Hawaii = 21:00 UTC
    text, label = appmod._reminder_when("2026-09-30T11:00:00", ct.HAWAII, LA)
    assert "14:00" in text or "2:00" in text


def test_the_staff_email_has_no_nested_brackets():
    b = {"start_ts": "2026-09-30T11:00:00", "end_ts": "2026-09-30T11:30:00", "ics_uid": "u2",
         "portal_url": "https://x/portal/t", "client_tz": LA}
    with mock.patch.object(appmod, "send_evox_email") as send:
        appmod._consult_send_confirmations("c2@x.com", b)
    glen = [c_ for c_ in send.call_args_list if c_.args[0] != "c2@x.com"][0].args[3]
    assert "Wed 30 Sep, 11:00 am Hawaii time" in glen and "2:00 pm" in glen
    assert "((" not in glen and "Hawaii))" not in glen


RENDER_JS = r"""
const assert = require('assert');
let seg = "tok", loads = 0, onb = 0;
function esc(s){ return String(s); }
function load(){ loads++; }
function initOnboardingCard(){ onb++; }
global.document = {getElementById: () => null};
BLOCK
browserZone = () => "Europe/London";
global.fetch = async () => ({ok: true, json: async () => ({tz: "America/New_York"})});
(async () => {
  await loadClientZone();
  assert.strictEqual(CLIENT_TZ, "America/New_York");
  assert.strictEqual(loads, 1);            // everything re-renders in the stored zone
  console.log('OK');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_portal_re_renders_when_the_stored_zone_differs(tmp_path):
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    a, b = page.find("// BEGIN client time zone"), page.find("// END client time zone")
    js = tmp_path / "r.js"
    js.write_text(RENDER_JS.replace("BLOCK", page[a:b]))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
