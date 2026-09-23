"""The rebuilt remedy match email: no AI text, one per chat after it goes quiet, one per
client per week, every body recorded, and never sent twice. Glen chose send-automatically
on 2026-09-22 (platform/plans/2026-09-22-remedy-match-email-plan)."""
import sqlite3
from datetime import datetime, timedelta, timezone

from dashboard import remedy_match_email as rme

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    return cx


def _enq(cx, session="s1", product="Microbiome", slug="microbiome", at=T0,
         email="maria@example.com"):
    return rme.enqueue(cx, email=email, name="Maria Sutryn", session_id=session,
                       product_slug=slug, product_name=product,
                       page_url=f"https://illtowell.com/begin/product/{slug}", now=at)


class Sender:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
    def __call__(self, email, name, subject, html, text):
        if self.fail:
            raise RuntimeError("smtp down")
        self.calls.append({"email": email, "subject": subject, "html": html, "text": text})


def test_nothing_sends_until_the_chat_has_been_quiet():
    cx, send = _cx(), Sender()
    _enq(cx)
    assert rme.drain(cx, send, now=T0 + timedelta(minutes=29))["sent"] == 0
    assert rme.drain(cx, send, now=T0 + timedelta(minutes=30))["sent"] == 1


def test_a_chat_that_moves_between_options_sends_only_the_last_one():
    """Maria's chat named 11 things. She gets one email, for where it ended."""
    cx, send = _cx(), Sender()
    for i, p in enumerate(["OcuHeal", "Avenova", "Vitreous Vitality", "Microbiome"]):
        _enq(cx, product=p, slug=p.lower().replace(" ", "-"), at=T0 + timedelta(minutes=i))
    rme.drain(cx, send, now=T0 + timedelta(hours=2))
    assert len(send.calls) == 1 and send.calls[0]["subject"] == "Your remedy match: Microbiome"


def test_one_email_per_chat_even_if_it_names_another_later():
    cx, send = _cx(), Sender()
    _enq(cx)
    rme.drain(cx, send, now=T0 + timedelta(hours=1))
    assert _enq(cx, product="Transform", slug="transform", at=T0 + timedelta(hours=2)) is False
    rme.drain(cx, send, now=T0 + timedelta(hours=5))
    assert len(send.calls) == 1


def test_at_most_one_per_client_per_week():
    cx, send = _cx(), Sender()
    _enq(cx, session="s1")
    rme.drain(cx, send, now=T0 + timedelta(hours=1))
    _enq(cx, session="s2", at=T0 + timedelta(days=3))
    out = rme.drain(cx, send, now=T0 + timedelta(days=3, hours=1))
    assert out["capped"] == 1 and len(send.calls) == 1
    _enq(cx, session="s3", at=T0 + timedelta(days=8))
    rme.drain(cx, send, now=T0 + timedelta(days=8, hours=1))
    assert len(send.calls) == 2


def test_the_email_carries_no_ai_text_only_name_and_link():
    cx, send = _cx(), Sender()
    _enq(cx)
    rme.drain(cx, send, now=T0 + timedelta(hours=1))
    body = send.calls[0]["text"]
    assert body == ("Aloha Maria,\n\nYour remedy match is Microbiome.\n\n"
                    "You can read about it and order it here:\n"
                    "https://illtowell.com/begin/product/microbiome\n\nAloha,\nDr. Glen")
    assert "arial black" in send.calls[0]["html"]


def test_every_send_is_recorded_with_its_exact_body():
    cx, send = _cx(), Sender()
    _enq(cx)
    rme.drain(cx, send, now=T0 + timedelta(hours=1))
    row = rme.recent(cx)[0]
    assert row["status"] == "sent" and row["body_text"] == send.calls[0]["text"]


def test_a_failed_send_is_recorded_and_not_retried_forever():
    cx = _cx()
    _enq(cx)
    out = rme.drain(cx, Sender(fail=True), now=T0 + timedelta(hours=1))
    assert out["failed"] == 1 and rme.recent(cx)[0]["status"] == "failed"
    ok = Sender()
    rme.drain(cx, ok, now=T0 + timedelta(hours=2))
    assert ok.calls == []


class _RacingConn:
    """Another scheduler process claims the row between our SELECT and our claim."""
    def __init__(self, cx):
        self._cx, self.raced = cx, False
    def execute(self, sql, params=()):
        if "SET status='sending'" in sql and not self.raced:
            self.raced = True
            self._cx.execute("UPDATE remedy_match_email_queue SET status='sending'")
        return self._cx.execute(sql, params)
    def commit(self):
        self._cx.commit()


def test_a_row_another_process_claims_mid_drain_is_not_sent_twice():
    cx, send = _cx(), Sender()
    _enq(cx)
    racing = _RacingConn(cx)
    rme.drain(racing, send, now=T0 + timedelta(hours=1))
    assert racing.raced and send.calls == [], "REGRESSION: both processes would send"


def test_nothing_is_enqueued_without_a_page_or_a_session():
    cx = _cx()
    assert rme.enqueue(cx, email="a@example.com", name="", session_id="", product_slug="x",
                       product_name="X", page_url="https://x") is False
    assert rme.enqueue(cx, email="a@example.com", name="", session_id="s", product_slug="x",
                       product_name="X", page_url="") is False
