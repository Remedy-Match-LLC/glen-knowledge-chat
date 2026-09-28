"""The Live Calendar link correction for Sunday's invitation (Glen, 2026-09-27).

Sunday's invitation for 30 September linked portal Home, which shows no sessions and no
Reserve or Join button. The correction must reach ONLY the people that campaign reached,
who are also on a fresh consent list, once each, and never before the live portal routes
#calendar. Every test here runs offline against a temporary database: nothing reaches
GoHighLevel.
"""
import importlib.util
import os
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")

SPEC = importlib.util.spec_from_file_location(
    "weekly_live_invitation_c", Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
weekly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(weekly)

ORIGINAL = "weekly-live-community-2026-09-30"
CORRECTION = ORIGINAL + "-correction"


@pytest.fixture
def world(monkeypatch, tmp_path):
    dbfile = str(tmp_path / "log.db")
    monkeypatch.setattr(weekly.appmod.db, "connect", lambda _path: sqlite3.connect(dbfile))
    monkeypatch.setattr(weekly, "_authoritative_access_sets", lambda: ({"paid@x.com"}, set()))
    monkeypatch.setattr(weekly.client_portal, "ensure_token", lambda cx, email, name: "tok-" + email)
    suppressed = set()
    monkeypatch.setattr(weekly.email_suppression, "is_suppressed", lambda cx, e: e in suppressed)
    # never@x.com IS on the consent list: only the reached filter can keep it out.
    consent = {"a@x.com", "b@x.com", "paid@x.com", "dnd@x.com", "sup@x.com", "never@x.com"}
    monkeypatch.setattr(weekly.allowlist, "decode", lambda s: set(consent))
    monkeypatch.setattr(weekly.allowlist, "allows", lambda fp, e: e in fp)
    with sqlite3.connect(dbfile) as cx:
        weekly._init_run_tables(cx)
        for email, status in (("a@x.com", "queued"), ("b@x.com", "sent"), ("paid@x.com", "queued"),
                              ("dnd@x.com", "queued"), ("sup@x.com", "queued"),
                              ("gone@x.com", "queued"),        # reached, not on today's consent list
                              ("never@x.com", "failed")):      # the original never reached them
            weekly._record_recipient(cx, ORIGINAL, email, "", "", status)
    contacts = {e: {"id": "c-" + e, "firstName": "Pat", "email": e}
                for e in consent | {"gone@x.com", "never@x.com"}}
    contacts["dnd@x.com"]["dndSettings"] = {"Email": {"status": "active"}}
    sent = []

    def send(cid, subject, text, body_html, email_to="", scheduled_timestamp=None):
        sent.append({"to": email_to, "subject": subject, "text": text, "html": body_html})
        return 200, "m-" + email_to, {}

    kw = dict(portal_check=lambda: True, find_contact=lambda e: contacts.get(e),
              send=send, sleep=lambda s: None)
    return SimpleNamespace(db=dbfile, sent=sent, kw=kw, suppressed=suppressed, contacts=contacts)


def _args(**over):
    base = dict(date="2026-09-30", dry_run=False, send=True, only_list="LIST", correction=True)
    base.update(over)
    return SimpleNamespace(**base)


def _status(world, email):
    with sqlite3.connect(world.db) as cx:
        row = cx.execute("SELECT status FROM weekly_live_invitation_recipients "
                         "WHERE campaign_id=? AND email=?", (CORRECTION, email)).fetchone()
    return row[0] if row else None


def test_only_people_the_original_reached_and_still_consenting_get_it(world):
    world.suppressed.add("sup@x.com")
    rc = weekly.run_correction(_args(), **world.kw)
    got = sorted(m["to"] for m in world.sent)
    assert got == ["a@x.com", "b@x.com", "paid@x.com"], got
    assert rc == 0
    assert _status(world, "dnd@x.com") == "dnd" and _status(world, "sup@x.com") == "suppressed"
    assert _status(world, "gone@x.com") is None and _status(world, "never@x.com") is None


def test_a_second_run_sends_nothing(world):
    weekly.run_correction(_args(), **world.kw)
    first = len(world.sent)
    weekly.run_correction(_args(), **world.kw)
    assert len(world.sent) == first, "somebody got the correction twice"


def test_it_refuses_until_the_live_portal_routes_calendar(world):
    world.kw["portal_check"] = lambda: False
    with pytest.raises(RuntimeError, match="does not route #calendar"):
        weekly.run_correction(_args(), **world.kw)
    assert world.sent == []


def test_it_refuses_without_a_fresh_consent_list(world):
    with pytest.raises(RuntimeError, match="--only-list"):
        weekly.run_correction(_args(only_list=None), **world.kw)
    assert world.sent == []


def test_a_dry_run_sends_nothing_and_writes_nothing(world, capsys):
    weekly.run_correction(_args(dry_run=True, send=False), **world.kw)
    assert world.sent == [] and _status(world, "a@x.com") is None
    assert '"would_check": 5' in capsys.readouterr().out  # never@x.com was not reached


def test_an_unanswered_send_stops_the_run(world):
    calls = []

    def flaky(cid, subject, text, body_html, email_to="", scheduled_timestamp=None):
        calls.append(email_to)
        return 0, "", {}
    world.kw["send"] = flaky
    rc = weekly.run_correction(_args(), **world.kw)
    assert rc == 3 and len(calls) == 1
    assert _status(world, calls[0]) == "unknown"


def test_the_email_carries_the_calendar_link_times_and_the_right_access(world):
    weekly.run_correction(_args(), **world.kw)
    by = {m["to"]: m for m in world.sent}
    a, paid = by["a@x.com"], by["paid@x.com"]
    assert "/portal/tok-a@x.com#calendar" in a["text"]
    assert 'portal/tok-a@x.com#calendar">' in a["html"]
    assert "2:00 PM Hawaii · 5:00 PM Pacific · 8:00 PM Eastern" in a["text"]
    # not eligible: the Group Coaching time is not listed at all
    assert "Group Coaching" not in a["text"].split("With aloha")[0]
    assert "Group Coaching: 3:00 PM Hawaii · 6:00 PM Pacific · 9:00 PM Eastern" in paid["text"]
    assert "included with your certification or active full membership" in paid["text"]
    assert "The MasterClass is on Wednesday, September 30." in a["text"]
    assert "The sessions are on Wednesday, September 30." in paid["text"]
    assert "If you already reserved your spot, you are all set." in a["text"]
    assert a["subject"] == "Corrected link: the Live Calendar for Wednesday, September 30"
    for m in world.sent:
        assert not any(d in m["subject"] + m["text"] + m["html"] for d in weekly.EM_DASH_FORMS)


def test_the_portal_check_reads_the_live_route_table():
    assert weekly._live_portal_routes_calendar(
        lambda url: 'x calendar: {panel:"calendar", target:"calendar-card"} y')
    assert not weekly._live_portal_routes_calendar(lambda url: "finder: {panel:\"finder\"}")

    def down(url):
        raise OSError("unreachable")
    assert not weekly._live_portal_routes_calendar(down)



# --- round 1 review findings ------------------------------------------------------------

def test_a_second_job_while_one_is_sending_is_refused(world):
    with sqlite3.connect(world.db) as cx:
        cx.execute("INSERT INTO weekly_live_invitation_runs (campaign_id,status,updated_at) "
                   "VALUES (?,?,?)", (CORRECTION, "sending", weekly._now()))
    with pytest.raises(RuntimeError, match="already sending"):
        weekly.run_correction(_args(), **world.kw)
    assert world.sent == []


def test_a_stale_sending_row_does_not_block_forever(world):
    with sqlite3.connect(world.db) as cx:
        cx.execute("INSERT INTO weekly_live_invitation_runs (campaign_id,status,updated_at) "
                   "VALUES (?,?,?)", (CORRECTION, "sending", "2026-09-01T00:00:00+00:00"))
    assert weekly.run_correction(_args(), **world.kw) == 0


def test_a_person_the_run_cannot_place_marks_it_for_attention(world):
    world.contacts.pop("b@x.com")
    rc = weekly.run_correction(_args(), **world.kw)
    assert rc == 2, "a reached person was left out and the run still reported success"


def test_a_failed_lookup_stops_the_run(monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", "loc")
    monkeypatch.setattr(weekly, "_api", lambda *a, **k: (429, {}))
    with pytest.raises(RuntimeError, match="lookup failed"):
        weekly._lookup_contact("a@x.com")


def test_the_lookup_insists_on_the_same_address(monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", "loc")
    monkeypatch.setattr(weekly, "_api", lambda *a, **k: (200, {"contacts": [{"email": "Other@x.com"}]}))
    assert weekly._lookup_contact("a@x.com") is None
    monkeypatch.setattr(weekly, "_api", lambda *a, **k: (200, {"contacts": [{"email": "A@X.com"}]}))
    assert weekly._lookup_contact("a@x.com")["email"] == "A@X.com"


def test_a_chip_label_is_not_used_as_a_name():
    assert weekly._greeting_name("Sharper vision") == ""
    assert weekly._greeting_name("Mary-Jane") == "Mary-Jane"
    text, _ = weekly._correction_copy("Sharper vision", "https://h/portal/t#calendar", False,
                                      date(2026, 9, 30))
    assert text.startswith("Aloha,\n")


def test_real_fingerprints_select_the_audience(monkeypatch, world):
    """The stubs above replace allowlist matching. This runs the real HMAC path."""
    # A fresh copy: the fixture's stubs are on the shared module object.
    spec = importlib.util.spec_from_file_location(
        "allowlist_real", Path(__file__).parents[1] / "scripts" / "live_invitation_allowlist.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    monkeypatch.setattr(weekly, "allowlist", real)
    monkeypatch.setenv("CONSOLE_SECRET", "test-key")
    arg, _ = real.encode(["a@x.com", "paid@x.com"], key="test-key")
    weekly.run_correction(_args(only_list=arg), **world.kw)
    assert sorted(m["to"] for m in world.sent) == ["a@x.com", "paid@x.com"]


def test_the_portal_check_matches_the_real_route_line():
    html = (Path(__file__).parents[1] / "static" / "client-portal.html").read_text()
    assert weekly._live_portal_routes_calendar(lambda url: html)



# --- round 2 review findings ------------------------------------------------------------

def test_accepted_without_an_id_is_recorded_unknown_and_not_retried(world):
    world.kw["send"] = lambda cid, subject, text, body_html, email_to="", scheduled_timestamp=None: \
        (200, "", {"ok": True})
    rc = weekly.run_correction(_args(), **world.kw)
    assert rc == 2 and _status(world, "a@x.com") == "unknown"
    sent_again = []
    world.kw["send"] = lambda cid, subject, text, body_html, email_to="", scheduled_timestamp=None: \
        (sent_again.append(email_to), (200, "m", {}))[1]
    weekly.run_correction(_args(), **world.kw)
    assert "a@x.com" not in sent_again, "a maybe-queued reader was mailed again"


def test_a_dry_run_works_before_the_fix_is_live_and_says_so(world, capsys):
    world.kw["portal_check"] = lambda: False
    assert weekly.run_correction(_args(dry_run=True, send=False), **world.kw) == 0
    out = capsys.readouterr().out
    assert '"portal_routes_calendar": false' in out and '"group_eligible": 1' in out
    assert world.sent == []


def test_a_failed_lookup_mid_run_sends_nothing(world):
    def boom(email):
        raise RuntimeError("GHL contact lookup failed (429); stopping the correction")
    world.kw["find_contact"] = boom
    with pytest.raises(RuntimeError, match="lookup failed"):
        weekly.run_correction(_args(), **world.kw)
    assert world.sent == []


def test_the_unsubscribe_footer_is_on_both_parts(world):
    weekly.run_correction(_args(), **world.kw)
    m = world.sent[0]
    assert "unsubscribe" in m["text"].lower() and "unsubscribe" in m["html"].lower()


def test_later_batches_are_spaced_fifteen_minutes(world, monkeypatch):
    many = [f"p{i:03d}@x.com" for i in range(150)]
    with sqlite3.connect(world.db) as cx:
        for e in many:
            weekly._record_recipient(cx, ORIGINAL, e, "", "", "queued")
    monkeypatch.setattr(weekly.allowlist, "decode", lambda s: set(many))
    for e in many:
        world.contacts[e] = {"id": "c-" + e, "firstName": "Pat", "email": e}
    stamps = []
    world.kw["send"] = lambda cid, subject, text, body_html, email_to="", scheduled_timestamp=None: \
        (stamps.append(scheduled_timestamp), (200, "m-" + email_to, {}))[1]
    weekly.run_correction(_args(), **world.kw)
    assert stamps[:100] == [None] * 100
    assert len(set(stamps[100:])) == 1 and stamps[100] is not None


def test_a_shouted_name_is_softened():
    assert weekly._greeting_name("JOHN") == "John"
