"""The remedy-match confirmation goes out when someone agrees to the Terms (Glen,
2026-10-29: "yes"). It used to wait for the chat to find a remedy, so 11 of 12 sign-ups
from 15 to 28 October were never asked to confirm. One client wrote that it never came.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import sqlite3

import pytest

import app
from dashboard import remedy_match_email as rme

T0 = datetime(2026, 10, 29, 21, 0, tzinfo=timezone.utc)
URL = lambda t: "https://illtowell.com/begin/confirm-email?t=" + t


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "q.db"))
    rme.init(cx)
    return cx


def _drain(cx, now=T0):
    sent = []
    out = rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=now)
    return out, sent


# ── the module ──────────────────────────────────────────────────────────────
def test_a_terms_request_sends_one_signup_confirmation(tmp_path):
    cx = _cx(tmp_path)
    assert rme.request_confirm(cx, " New@Example.com ")
    out, sent = _drain(cx)
    assert out["confirm_sent"] == 1
    (email, _, subject, html, text), = sent
    assert email == "new@example.com"
    assert subject == rme.SIGNUP_CONFIRM_SUBJECT
    assert "/begin/confirm-email?t=" in text and "Confirm my email" in html
    assert "you found in our chat" not in text      # no remedy has been found yet


def test_it_is_sent_once_however_often_the_drain_runs(tmp_path):
    cx = _cx(tmp_path)
    rme.request_confirm(cx, "a@example.com")
    _drain(cx)
    for h in (1, 24, 24 * 30):
        assert _drain(cx, T0 + timedelta(hours=h))[1] == []


def test_a_second_signup_does_not_send_again(tmp_path):
    cx = _cx(tmp_path)
    rme.request_confirm(cx, "a@example.com")
    _drain(cx)
    assert not rme.request_confirm(cx, "a@example.com")
    assert _drain(cx, T0 + timedelta(hours=1))[1] == []


def test_a_confirmed_address_is_never_asked(tmp_path):
    cx = _cx(tmp_path)
    rme.mark_confirmed(cx, "a@example.com", now=T0)
    assert not rme.request_confirm(cx, "a@example.com")
    assert _drain(cx)[1] == []


def test_a_waiting_match_keeps_the_remedy_wording(tmp_path):
    cx = _cx(tmp_path)
    rme.request_confirm(cx, "a@example.com")
    rme.enqueue(cx, email="a@example.com", name="", session_id="s1",
                product_slug="clear-the-way", product_name="Clear the Way",
                page_url="https://illtowell.com/begin/product/clear-the-way", now=T0)
    out, sent = _drain(cx, T0 + timedelta(hours=1))
    assert out["confirm_sent"] == 1 and len(sent) == 1
    assert sent[0][2] == rme.CONFIRM_SUBJECT


def test_clicking_the_signup_link_confirms_and_a_later_match_goes_straight_out(tmp_path):
    cx = _cx(tmp_path)
    rme.request_confirm(cx, "a@example.com")
    _, sent = _drain(cx)
    token = sent[0][4].split("?t=")[1].split()[0]
    assert rme.confirm(cx, token, now=T0 + timedelta(hours=2)) == "a@example.com"
    rme.enqueue(cx, email="a@example.com", name="", session_id="s1",
                product_slug="clear-the-way", product_name="Clear the Way",
                page_url="https://illtowell.com/begin/product/clear-the-way",
                now=T0 + timedelta(hours=3))
    out, sent = _drain(cx, T0 + timedelta(hours=4))
    assert out["sent"] == 1 and sent[0][2].endswith("Clear the Way")


def test_a_bad_address_is_not_recorded(tmp_path):
    cx = _cx(tmp_path)
    assert not rme.request_confirm(cx, "nope")
    assert not rme.request_confirm(cx, "")


# ── the routes ──────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "1")
    import begin_funnel
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:
        begin_funnel.init_journey_tables(cx)
        cx.execute("CREATE TABLE IF NOT EXISTS people (name TEXT, email TEXT)")
    monkeypatch.setattr(app, "ghl_onboard_contact", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app, "_active_membership_for_email", lambda e: None)
    c = app.app.test_client()
    c.set_cookie("amg_session", "sess-test-1", domain="localhost")
    return c


def _requested(email):
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:
        rme.init(cx)
        return cx.execute("SELECT COUNT(*) FROM remedy_match_email_confirm WHERE email=? "
                          "AND sent_at IS NULL AND confirmed_at IS NULL",
                          (email,)).fetchone()[0]


def test_agreeing_to_the_terms_requests_the_confirmation(client):
    r = client.post("/begin/unlock", json={"trigger": "tos", "email": "Visitor@Example.com",
                                           "tos": True})
    assert r.status_code == 200
    assert _requested("visitor@example.com") == 1


def test_an_email_without_the_terms_requests_nothing(client):
    client.post("/begin/unlock", json={"trigger": "email", "email": "nope@example.com"})
    assert _requested("nope@example.com") == 0


def test_the_flag_off_requests_nothing(client, monkeypatch):
    monkeypatch.setenv("REMEDY_MATCH_EMAIL_ENABLED", "")
    client.post("/begin/unlock", json={"trigger": "tos", "email": "a@example.com", "tos": True})
    assert _requested("a@example.com") == 0


def test_a_suppressed_address_is_never_asked(client):
    from dashboard import db, email_suppression as es
    with db.connect(app.LOG_DB) as cx:
        es.init_table(cx)
        es.add(cx, "gone@example.com", "hard", "bounce", "test")
    client.post("/begin/unlock", json={"trigger": "tos", "email": "gone@example.com",
                                       "tos": True})
    assert _requested("gone@example.com") == 0


def test_a_failure_never_breaks_the_signup(client, monkeypatch):
    monkeypatch.setattr(rme, "request_confirm", lambda *a, **k: 1 / 0)
    r = client.post("/begin/unlock", json={"trigger": "tos", "email": "a@example.com",
                                           "tos": True})
    assert r.status_code == 200


def test_every_terms_opt_in_route_requests_it():
    """quiz, fireside and doorway opt-ins write the Terms the same way as unlock."""
    import inspect
    for fn in (app.begin_quiz_optin, app.begin_fireside_optin, app.begin_doorway_optin,
               app.begin_unlock):
        assert "_want_remedy_confirm(cx, email)" in inspect.getsource(fn), fn.__name__
