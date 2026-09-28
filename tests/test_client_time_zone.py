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
    assert c.post(f"/api/portal/time-zone?token={tok}", json={"tz": LA, "source": "browser"}).status_code == 200
    assert _zone(f"tz1-{RUN}@x.com") == LA
    c.post(f"/api/portal/time-zone?token={tok}", json={"tz": "Europe/London", "source": "chosen"})
    assert _zone(f"tz1-{RUN}@x.com") == "Europe/London"
    c.post(f"/api/portal/time-zone?token={tok}", json={"tz": LA, "source": "browser"})
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
    assert "11:00 am Hawaii time" in glen.args[3] and "client's time" in glen.args[3]
