# tests/test_e4l_bridge.py
"""The E4L bridge: an on-domain stop between our funnel and the Energy4Life signup.

Before it, every "start your voice scan" chip dropped the client straight onto
portal.e4l.com's signup: a page titled Sign Up rather than Voice Scan, eight
required fields including a full address and a password, a total brand break, and
no way back. Nothing carried over, so the name and the outcome they had just
typed into our chat were asked for again.

`_has_e4l` matches purely on email across journey_state and scan_freshness, so
capturing the email on OUR side before the jump is what lets the asynchronous
scan-freshness ingest close the loop and mark the scan step done.

`/begin/scan` is now the single destination every emitter points at. The flag
decides what it DOES, not what gets emitted: off, it passes straight through to
truly.vip/E4L exactly as today, one redirect later.
"""

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
E4L_SIGNUP = "https://truly.vip/E4L"
PORTAL = "https://portal.e4l.com"


def _load_app():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app module not importable in this env: {e}")


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    app = _load_app()
    db = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", db)
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
    return app, db


def _on(app, monkeypatch):
    monkeypatch.setattr(app, "E4L_BRIDGE_ENABLED", True)


def _off(app, monkeypatch):
    monkeypatch.setattr(app, "E4L_BRIDGE_ENABLED", False)


# ---------------------------------------------------------------------------
# Flag off: today's behavior, one redirect later
# ---------------------------------------------------------------------------

def test_flag_off_passes_straight_through(app_db, monkeypatch):
    app, _ = app_db
    _off(app, monkeypatch)
    r = app.app.test_client().get("/begin/scan")
    assert r.status_code == 302
    assert r.headers["Location"].startswith(E4L_SIGNUP)


def test_flag_off_still_threads_the_ref(app_db, monkeypatch):
    """Attribution must survive the pass-through or affiliates lose credit."""
    app, _ = app_db
    _off(app, monkeypatch)
    c = app.app.test_client()
    c.set_cookie("rm_ref", "jane")
    loc = c.get("/begin/scan").headers["Location"]
    assert "utm_source=jane" in loc and "utm_medium=affiliate" in loc


def test_flag_off_renders_no_bridge(app_db, monkeypatch):
    """Flask's 302 carries its own tiny HTML body, so assert OUR page is absent
    rather than that no markup came back."""
    app, _ = app_db
    _off(app, monkeypatch)
    r = app.app.test_client().get("/begin/scan")
    assert b"Heads up before you go" not in r.data
    assert b"window.__SCAN__" not in r.data


# ---------------------------------------------------------------------------
# Flag on: the bridge itself
# ---------------------------------------------------------------------------

def test_bridge_renders_and_names_what_e4l_will_ask_for(app_db, monkeypatch):
    """The whole point: no ambush. If we do not say a password and an address
    are coming, the bridge has not done its job."""
    app, _ = app_db
    _on(app, monkeypatch)
    html = app.app.test_client().get("/begin/scan").get_data(as_text=True).lower()
    assert "password" in html
    assert "address" in html


def test_bridge_offers_a_skip(app_db, monkeypatch):
    """A free scan is not worth a wall. The email ask never blocks."""
    app, _ = app_db
    _on(app, monkeypatch)
    html = app.app.test_client().get("/begin/scan").get_data(as_text=True)
    payload = _payload(html)
    assert payload["skip_href"].startswith(E4L_SIGNUP)


def _payload(html):
    marker = "window.__SCAN__ = "
    i = html.index(marker) + len(marker)
    return json.loads(html[i:html.index("</script>", i)].strip().rstrip(";"))


def test_bridge_sends_a_new_visitor_to_the_signup(app_db, monkeypatch):
    app, _ = app_db
    _on(app, monkeypatch)
    p = _payload(app.app.test_client().get("/begin/scan").get_data(as_text=True))
    assert p["href"].startswith(E4L_SIGNUP)
    assert p["has_e4l"] is False
    assert p["email"] == ""


def test_bridge_threads_the_ref_onto_the_handoff(app_db, monkeypatch):
    app, _ = app_db
    _on(app, monkeypatch)
    c = app.app.test_client()
    c.set_cookie("rm_ref", "jane")
    p = _payload(c.get("/begin/scan").get_data(as_text=True))
    assert "utm_source=jane" in p["href"]


def test_campaign_tag_survives_the_extra_hop(app_db, monkeypatch):
    """Every emitter now points at one URL, so each passes its own ?c= or the
    per-surface analytics granularity is lost at the bridge."""
    app, _ = app_db
    _on(app, monkeypatch)
    p = _payload(app.app.test_client().get("/begin/scan?c=begin-match-e4l").get_data(as_text=True))
    assert "utm_campaign=begin-match-e4l" in p["href"]


def test_an_unknown_campaign_falls_back_and_is_never_injected(app_db, monkeypatch):
    app, _ = app_db
    _on(app, monkeypatch)
    p = _payload(app.app.test_client().get("/begin/scan?c=%3Cscript%3E").get_data(as_text=True))
    assert "utm_campaign=begin-scan" in p["href"]
    assert "<script>" not in p["href"]


# ---------------------------------------------------------------------------
# What we already know about them
# ---------------------------------------------------------------------------

def _seed_email(db, email, gates=()):
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
        cx.execute(
            "INSERT INTO journey_state (session_id, email, unlocked_gates, current_rung, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("sess-seed", email, json.dumps(list(gates)), "personalize",
             "2026-08-20T00:00:00Z", "2026-08-20T00:00:00Z"))
        cx.commit()


def test_a_known_email_is_prefilled(app_db, monkeypatch):
    app, db = app_db
    _on(app, monkeypatch)
    _seed_email(db, "jane@example.com")
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-seed")
    p = _payload(c.get("/begin/scan").get_data(as_text=True))
    assert p["email"] == "jane@example.com"


def test_a_returning_scanner_goes_to_the_portal_not_the_signup(app_db, monkeypatch):
    """The #863 half-fix, finished: someone who already has an E4L account must
    never be sent to create a second one."""
    app, db = app_db
    _on(app, monkeypatch)
    _seed_email(db, "jane@example.com", gates=["scan"])
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-seed")
    p = _payload(c.get("/begin/scan").get_data(as_text=True))
    assert p["has_e4l"] is True
    assert p["href"].startswith(PORTAL)
    assert not p["href"].startswith(E4L_SIGNUP)


# ---------------------------------------------------------------------------
# The return must not become a self-serve gate
# ---------------------------------------------------------------------------

def test_the_bridge_never_sets_the_scan_gate_itself(app_db, monkeypatch):
    """Self-attestation would let anyone skip a rung. The scan gate stays with
    the scan-freshness ingest, which is authoritative."""
    app, db = app_db
    _on(app, monkeypatch)
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-bridge")
    c.get("/begin/scan")
    with sqlite3.connect(db) as cx:
        row = cx.execute("SELECT unlocked_gates FROM journey_state WHERE session_id=?",
                         ("sess-bridge",)).fetchone()
    assert "scan" not in set(json.loads((row or ["[]"])[0] or "[]"))


# ---------------------------------------------------------------------------
# Every emitter points at the bridge
# ---------------------------------------------------------------------------

def test_no_funnel_surface_links_the_signup_directly():
    """Eleven places emitted truly.vip/E4L. A direct link is a client dropped on
    a signup wall with no preparation and no way back."""
    offenders = []
    for rel in ("begin_funnel.py", "static/begin-voice.html", "static/begin-match.html",
                "static/concierge.html", "static/index.html", "data/trusted-links.json"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "truly.vip/E4L" in line:
                offenders.append(f"{rel}:{i}")
    assert offenders == [], offenders


def test_the_card_and_deep_link_resolve_to_the_bridge():
    import begin_funnel as bf
    assert bf.CARD_CATALOG["e4l_scan"]["base_url"].startswith("/begin/scan")
    assert (bf.resolve_want("e4l") or "").startswith("/begin/scan")


def test_the_journey_map_scan_step_resolves_to_the_bridge():
    import begin_funnel as bf
    state = {"unlocked_gates": [], "current_rung": "arrival"}
    scan = {c["key"]: c for c in bf.journey_map(state, ref="jane", signals={})}["scan"]
    assert scan["href"].startswith("/begin/scan")


def test_the_portal_shortcut_is_decided_in_one_place_now():
    """#863 threaded `signals` through journey_map so a returning client's Scan
    chip resolved to the portal instead of the signup. The bridge does that same
    _has_e4l lookup itself, so the map hands off unconditionally and there is ONE
    place that decides portal-vs-signup instead of two that can disagree."""
    import begin_funnel as bf
    state = {"unlocked_gates": [], "current_rung": "arrival"}
    for signals in ({}, {"has_e4l": True}):
        scan = {c["key"]: c for c in bf.journey_map(state, ref="", signals=signals)}["scan"]
        assert scan["href"].startswith("/begin/scan")


def test_ref_survives_a_deep_link_with_no_cookie(app_db, monkeypatch):
    """A ?want=e4l deep link records its ref into journey_state and never sets
    rm_ref. Reading only the cookie would drop the affiliate's credit at the
    bridge -- the one hop the whole referral is threaded through."""
    app, db = app_db
    _on(app, monkeypatch)
    import begin_funnel
    with sqlite3.connect(db) as cx:
        begin_funnel.init_journey_tables(cx)
        begin_funnel.record_unlock(cx, session_id="sess-deep", trigger="deep_link",
                                   ref_slug="jane", want="e4l")
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-deep")
    p = _payload(c.get("/begin/scan").get_data(as_text=True))
    assert "utm_source=jane" in p["href"]
