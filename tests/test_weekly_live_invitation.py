import importlib.util
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")

SPEC = importlib.util.spec_from_file_location(
    "weekly_live_invitation",
    Path(__file__).parents[1] / "scripts" / "weekly_live_invitation.py")
weekly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(weekly)


def test_copy_routes_only_through_private_portal():
    text, body = weekly._copy(
        "Glen", "https://myhealingoasis.com/portal/private-token", True,
        date(2026, 8, 26))
    assert "Group Coaching is included" in text
    assert "https://myhealingoasis.com/portal/private-token" in text
    assert "zoom.us/" not in (text + body).lower()
    assert "Practice Better" not in text
    assert "Skool" not in text


def test_free_copy_states_group_coaching_is_upgrade_benefit():
    text, _ = weekly._copy(
        "Friend", "https://myhealingoasis.com/portal/private-token", False,
        date(2026, 8, 26))
    assert "MasterClass is open to you" in text
    assert "upgrade benefit" in text
    assert "current access does not include" in text


def test_send_uses_write_token(monkeypatch):
    seen = {}

    def fake_api(method, path, version, body=None, *, write=False):
        seen.update(method=method, path=path, version=version, body=body, write=write)
        return 201, {"messageId": "msg-1"}

    monkeypatch.setattr(weekly, "_api", fake_api)
    status, message_id, _ = weekly._send(
        "contact-1", "Subject", "plain", "<p>html</p>",
        email_to="member@example.com", scheduled_timestamp=1_800_000_000)

    assert status == 201
    assert message_id == "msg-1"
    assert seen["write"] is True
    assert seen["body"]["emailFrom"] == weekly.FROM_ADDRESS
    assert seen["body"]["emailTo"] == "member@example.com"
    assert seen["body"]["scheduledTimestamp"] == 1_800_000_000


def test_create_contact_reuses_contact_when_email_is_an_additional_address(monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", "location-1")
    monkeypatch.setattr(
        weekly, "_api",
        lambda *args, **kwargs: (
            400,
            {"message": "This location does not allow duplicated contacts.",
             "meta": {"contactId": "existing-1", "matchingField": "additionalEmail"}},
        ),
    )

    contact = weekly._create_contact("member@example.com")

    assert contact == {"id": "existing-1", "email": "member@example.com"}


def test_send_preserves_string_error_response_without_crashing(monkeypatch):
    monkeypatch.setattr(
        weekly, "_api",
        lambda *args, **kwargs: (422, {"message": "email address is invalid"}),
    )

    status, message_id, response = weekly._send(
        "contact-1", "Subject", "plain", "<p>html</p>",
        email_to="bad@example.com")

    assert status == 422
    assert message_id == ""
    assert response["message"] == "email address is invalid"


# Glen, 2026-09-13: no em dashes in the invitation, subject or body.
_EMDASH = "—"
_ARGS = SimpleNamespace(date="2026-09-16", dry_run=True, send=False)


class _GateReached(Exception):
    pass


def _patch_footer_secret(monkeypatch):
    from dashboard import unsubscribe
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def _gate_that_records(monkeypatch):
    calls = []

    def gate(target_date):
        calls.append(target_date)
        raise _GateReached()

    monkeypatch.setattr(weekly, "_event_gate", gate)
    return calls


def test_subject_and_both_bodies_carry_no_em_dash():
    assert _EMDASH not in weekly._subject(date(2026, 9, 16))
    for eligible in (True, False):
        text, body = weekly._copy(
            "Friend", "https://myhealingoasis.com/portal/private-token", eligible,
            date(2026, 9, 16))
        assert _EMDASH not in text
        assert _EMDASH not in body


def test_subject_still_reads_as_the_invitation_to_the_duplicate_gate():
    # The vault's check_week_sent.py matches "live community session" in the subject.
    assert "live community session" in weekly._subject(date(2026, 9, 16)).lower()


def test_clean_copy_passes_the_guard_and_reaches_the_event_gate(monkeypatch):
    _patch_footer_secret(monkeypatch)
    calls = _gate_that_records(monkeypatch)
    with pytest.raises(_GateReached):
        weekly.run(_ARGS)
    assert calls == [date(2026, 9, 16)]


def test_em_dash_in_subject_stops_the_run_before_the_event_gate(monkeypatch):
    _patch_footer_secret(monkeypatch)
    calls = _gate_that_records(monkeypatch)
    monkeypatch.setattr(weekly, "_subject", lambda d: f"Sessions {_EMDASH} September 16")
    with pytest.raises(RuntimeError, match="em dash found"):
        weekly.run(_ARGS)
    assert calls == []


def test_em_dash_in_body_stops_the_run_before_the_event_gate(monkeypatch):
    _patch_footer_secret(monkeypatch)
    calls = _gate_that_records(monkeypatch)
    real_copy = weekly._copy

    def copy_with_dash(*args, **kwargs):
        text, body = real_copy(*args, **kwargs)
        return text + f" {_EMDASH} ", body

    monkeypatch.setattr(weekly, "_copy", copy_with_dash)
    with pytest.raises(RuntimeError, match="em dash found"):
        weekly.run(_ARGS)
    assert calls == []



# --- the link lands on the Live Calendar, not Home -----------------------------------
# Robin Rohr, 2026-09-27: "no ability to get into a meeting". The bare portal link opens
# Home, which has no sessions and no Join button.

def test_the_portal_link_opens_the_live_calendar():
    w = weekly
    assert w.portal_link("tok").endswith("/portal/tok#calendar")


def test_the_send_loop_uses_the_calendar_link():
    import inspect
    w = weekly
    src = inspect.getsource(w)
    assert "portal_url = portal_link(token)" in src
    assert 'f"{PORTAL_BASE}/portal/{token}"' not in src


def test_the_copy_names_the_live_calendar():
    import datetime
    w = weekly
    text, _ = w._copy("Glen", w.portal_link("tok"), True, datetime.date(2026, 9, 30))
    assert "Live Calendar" in text and "#calendar" in text


def test_the_calendar_route_exists_in_the_portal():
    """The fragment is only useful if the portal routes it."""
    import os
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "static", "client-portal.html")).read()
    assert 'calendar: {panel:"calendar", target:"calendar-card", alt:"calendar-summary", openCard:true}' in html
    assert html.count("openCard:true") == 1, "only #calendar opens its card"


def test_the_email_link_is_an_explicit_anchor_with_the_fragment():
    """No linkifier guesswork: the HTML body carries <a href=".../portal/tok#calendar">."""
    import datetime
    w = weekly
    _, body = w._copy("Glen", w.portal_link("tok"), True, datetime.date(2026, 9, 30))
    assert '<a href="' in body and 'portal/tok#calendar">' in body


def test_a_deep_link_opens_its_card_over_the_fold_state():
    """#calendar would have landed on a folded Live Calendar card (review round 1,
    2026-09-27). The target is recorded before showTab and consumed by _foldApplyAll,
    which runs whenever the fold state arrives, so a late load cannot fold it again."""
    import os
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "static", "client-portal.html")).read()
    hash_fn = html[html.index("function applyPortalHash(userAction){"):html.index('window.addEventListener("hashchange", applyPortalHash);')]
    assert hash_fn.index("window._foldDeepLinkOpen = route.target;") < hash_fn.index("showTab(")
    # only routes that ask, and only when the link is followed (not on every re-render)
    assert "route.openCard && (userAction || window._portalHashOpened !== key)" in hash_fn
    # round 3 survivors: record the key, open synchronously, fall back, clicks are user actions
    assert "window._portalHashOpened = key;" in hash_fn
    assert 'if(typeof _foldOpenCard === "function") _foldOpenCard(route.target);' in hash_fn
    assert "(route.alt ? document.getElementById(route.alt) : null)" in hash_fn
    assert 'if(location.hash === "#" + key) applyPortalHash(true);' in html
    apply_fn = html[html.index("function _foldApplyAll(){"):html.index("function _foldToggle(card){")]
    assert "_deepLinked = window._foldDeepLinkOpen;" in apply_fn
    assert "F.setCard(_foldsV2.state, _deepLinked, false)" in apply_fn
    assert "_dl.scrollIntoView" in apply_fn, "re-scroll after the folds settle"
    assert "window._foldDeepLinkOpen = null" in apply_fn
    assert 'calendar: {panel:"calendar", target:"calendar-card", alt:"calendar-summary", openCard:true}' in html
    assert html.count("openCard:true") == 1, "only #calendar opens its card"



# --- times in Hawaii, Pacific and Eastern (Glen, 2026-09-27) --------------------------

def test_times_in_three_zones_during_mainland_daylight_time():
    import datetime
    assert weekly.session_times(datetime.date(2026, 9, 30), 14) == \
        "2:00 PM Hawaii · 5:00 PM Pacific · 8:00 PM Eastern"


def test_times_follow_the_mainland_clock_change():
    """Hawaii keeps no daylight time; the mainland falls back on 1 November 2026."""
    import datetime
    assert weekly.session_times(datetime.date(2026, 11, 4), 14) == \
        "2:00 PM Hawaii · 4:00 PM Pacific · 7:00 PM Eastern"


def test_the_copy_carries_both_sessions_in_three_zones_in_order():
    import datetime
    text, _ = weekly._copy("Glen", weekly.portal_link("tok"), True, datetime.date(2026, 9, 30))
    mc = text.index("Free Wellness Whispering MasterClass: 2:00 PM Hawaii · 5:00 PM Pacific · 8:00 PM Eastern")
    gc = text.index("Group Coaching: 3:00 PM Hawaii · 6:00 PM Pacific · 9:00 PM Eastern")
    assert mc < gc and "HST" not in text.split("With aloha")[0]



def test_the_html_body_writes_the_separator_as_an_entity():
    import datetime
    text, body = weekly._copy("Glen", weekly.portal_link("tok"), True, datetime.date(2026, 9, 30))
    assert "\u00b7" in text and "&middot;" in body and "\u00b7" not in body
