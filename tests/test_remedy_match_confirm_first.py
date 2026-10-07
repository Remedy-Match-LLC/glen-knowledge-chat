"""Confirm first (Glen, 2026-10-07). A remedy email to an address nobody has proven waits
for its owner to click a confirmation link. The confirmation never names the product."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import sqlite3

from dashboard import remedy_match_email as rme

T0 = datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc)
URL = lambda t: "https://illtowell.com/begin/confirm-email?t=" + t


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "q.db"))
    rme.init(cx)
    return cx


def _enqueue(cx, email="v@example.com", session="s1", proven=False, now=T0):
    return rme.enqueue(cx, email=email, name="Vee Test", session_id=session,
                       product_slug="clear-the-way", product_name="Clear the Way",
                       page_url="https://illtowell.com/begin/product/clear-the-way",
                       proven=proven, now=now)


def _status(cx, email="v@example.com", session="s1"):
    return cx.execute("SELECT status FROM remedy_match_email_queue WHERE email=? AND session_id=?",
                      (email, session)).fetchone()[0]


def test_an_unproven_address_gets_a_confirmation_not_the_remedy(tmp_path):
    cx = _cx(tmp_path)
    assert _enqueue(cx)
    assert _status(cx) == "awaiting_confirm"
    sent = []
    out = rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(hours=1))
    assert out["sent"] == 0 and out["confirm_sent"] == 1
    (email, name, subject, html, text), = sent
    assert email == "v@example.com" and subject == rme.CONFIRM_SUBJECT
    assert "Clear the Way" not in subject + html + text      # never names the product
    assert "/begin/confirm-email?t=" in text and "Confirm my email" in html
    assert text.startswith("Aloha,\n\nPlease confirm this is your email, and I'll send you "
                           "the link to the remedy you found in our chat:\n")
    assert text.endswith("\n\nIf you didn't chat with us, you can ignore this message."
                         "\n\nAloha,\nDr. Glen")
    assert "Vee" not in text + html          # no name typed by a visitor


def test_clicking_the_link_releases_the_remedy_email(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx)
    sent = []
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(minutes=1))
    token = sent[0][4].split("?t=")[1].split()[0]
    assert rme.confirm(cx, token, now=T0 + timedelta(minutes=5)) == "v@example.com"
    assert _status(cx) == "pending"
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(hours=1))
    assert sent[-1][2] == "The remedy you found in our chat: Clear the Way"
    assert _status(cx) == "sent"
    assert rme.confirm(cx, token, now=T0 + timedelta(hours=2)) == "v@example.com"   # 2nd click ok


def test_a_portal_sign_in_skips_confirmation_and_proves_the_address(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx, proven=True)
    assert _status(cx) == "pending"
    _enqueue(cx, session="s2")                   # later chat, same address, not signed in
    assert _status(cx, session="s2") == "pending"


def test_an_unconfirmed_match_is_dropped_after_a_week(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx)
    rme.drain(cx, lambda *a: None, confirm_url=URL, now=T0 + timedelta(days=8))
    assert _status(cx) == "skipped"


def test_a_bad_or_expired_token_confirms_nothing(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx)
    sent = []
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0)
    token = sent[0][4].split("?t=")[1].split()[0]
    assert rme.confirm(cx, "nope", now=T0) is None
    assert rme.confirm(cx, token, now=T0 + timedelta(days=8)) is None
    assert _status(cx) == "awaiting_confirm"
    assert not rme.is_confirmed(cx, "v@example.com")


def test_the_token_is_stored_only_as_a_hash(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx)
    sent = []
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0)
    token = sent[0][4].split("?t=")[1].split()[0]
    stored = cx.execute("SELECT token_hash FROM remedy_match_email_confirm").fetchone()[0]
    assert stored != token and stored == rme._hash(token)


def test_the_confirm_page_route(monkeypatch, tmp_path):
    """GET only shows a button (mail scanners open links); the POST confirms."""
    import app
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    from dashboard import db
    with db.connect(app.LOG_DB) as cx:
        _enqueue(cx, now=datetime.now(timezone.utc))
        sent = []
        rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=datetime.now(timezone.utc))
    token = sent[0][4].split("?t=")[1].split()[0]
    c = app.app.test_client()
    r = c.get("/begin/confirm-email?t=" + token)
    assert r.status_code == 200 and b"Confirm my email" in r.data
    with db.connect(app.LOG_DB) as cx:
        assert not rme.is_confirmed(cx, "v@example.com")       # a scanner's GET does nothing
    r = c.post("/begin/confirm-email", data={"t": token})
    assert r.status_code == 200 and b"confirmed" in r.data
    assert r.headers["Referrer-Policy"] == "no-referrer"
    with db.connect(app.LOG_DB) as cx:
        assert rme.is_confirmed(cx, "v@example.com")
    assert c.post("/begin/confirm-email", data={"t": "wrong"}).status_code == 410


def test_a_late_click_does_not_release_a_week_old_match(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx, now=T0)
    sent = []
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(days=6))
    token = sent[0][4].split("?t=")[1].split()[0]
    assert rme.confirm(cx, token, now=T0 + timedelta(days=8)) == "v@example.com"
    assert _status(cx) == "awaiting_confirm"
    rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(days=8))
    assert _status(cx) == "skipped" and len(sent) == 1


def test_a_match_written_as_waiting_after_confirmation_is_healed(tmp_path):
    """Race: another process confirmed the address between is_confirmed and the insert."""
    cx = _cx(tmp_path)
    rme.mark_confirmed(cx, "v@example.com", now=T0)
    cx.execute("INSERT INTO remedy_match_email_queue (email, session_id, name, product_slug, "
               "product_name, page_url, updated_at, status) VALUES "
               "('v@example.com','s9','V','clear-the-way','Clear the Way','u',?,'awaiting_confirm')",
               (T0.isoformat(),))
    cx.commit()
    rme.drain(cx, lambda *a: None, confirm_url=URL, now=T0 + timedelta(minutes=1))
    assert _status(cx, session="s9") == "pending"


def test_one_confirmation_per_address_per_week_however_many_chats(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx, session="s1")
    _enqueue(cx, session="s2", now=T0 + timedelta(days=3))
    sent = []
    for h in (0, 2, 26, 24 * 6):
        rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(hours=h))
    assert len(sent) == 1


def test_a_failed_confirmation_send_is_not_retried_every_minute(tmp_path):
    cx = _cx(tmp_path)
    _enqueue(cx)
    calls = []
    def boom(*a):
        calls.append(a)
        raise RuntimeError("raised after the server accepted it")
    rme.drain(cx, boom, confirm_url=URL, now=T0)
    rme.drain(cx, boom, confirm_url=URL, now=T0 + timedelta(minutes=1))
    rme.drain(cx, boom, confirm_url=URL, now=T0 + timedelta(hours=5))
    assert len(calls) == 1
    token = calls[0][4].split("?t=")[1].split()[0]
    assert rme.confirm(cx, token, now=T0 + timedelta(hours=6)) == "v@example.com"   # still works


def test_a_pending_row_from_before_confirm_first_is_held_back(tmp_path):
    cx = _cx(tmp_path)
    cx.execute("INSERT INTO remedy_match_email_queue (email, session_id, name, product_slug, "
               "product_name, page_url, updated_at, status) VALUES "
               "('old@example.com','s0','O','clear-the-way','Clear the Way','u',?,'pending')",
               (T0.isoformat(),))
    cx.commit()
    sent = []
    out = rme.drain(cx, lambda *a: sent.append(a), confirm_url=URL, now=T0 + timedelta(hours=1))
    assert out["sent"] == 0
    assert [a[2] for a in sent] == [rme.CONFIRM_SUBJECT]
    assert _status(cx, email="old@example.com", session="s0") == "awaiting_confirm"
