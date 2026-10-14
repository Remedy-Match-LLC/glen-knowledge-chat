"""A paying member is still invited to the sessions they pay for (Glen, 2026-09-13).

Only the People hub's consent:unsubscribed tag is waived, and only for the paid
and certification sets. Every suppression-table row still blocks, including an
unsubscribe clicked on the invitation itself.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import live_invitation_allowlist as allowlist
from types import SimpleNamespace

from dashboard import email_suppression as es

MEMBER = "member@x.com"


def _wli():
    # No skip on an import error: a broken sender must fail these tests, not hide them.
    spec = importlib.util.spec_from_file_location(
        "weekly_live_invitation_paid_unsub",
        Path(__file__).resolve().parents[1] / "scripts" / "weekly_live_invitation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cx(*, tagged=(), rows=()):
    cx = sqlite3.connect(":memory:")
    es.init_table(cx)
    cx.execute("CREATE TABLE people (email TEXT, tags TEXT)")
    for email in tagged:
        cx.execute("INSERT INTO people VALUES (?, ?)", (email, json.dumps(["consent:unsubscribed"])))
    for email, bounce_type, source in rows:
        es.add(cx, email, bounce_type, "test", source)
    cx.commit()
    return cx


def test_paid_member_with_only_the_hub_tag_is_invited():
    wli = _wli()
    cx = _cx(tagged=[MEMBER])
    assert es.is_suppressed(cx, MEMBER)            # the shared check still blocks
    assert wli._suppressed_for_invitation(cx, MEMBER, {MEMBER}) is False


def test_mixed_case_address_still_matches_the_paid_set():
    wli = _wli()
    cx = _cx(tagged=[MEMBER])
    padded = " Member@X.com "
    assert es.suppression_reason(cx, padded) == es.UNSUBSCRIBED_TAG   # the lookup sees it
    assert wli._suppressed_for_invitation(cx, padded, {MEMBER}) is False
    assert wli._suppressed_for_invitation(cx, padded, set()) is True
    cx2 = _cx(tagged=[MEMBER], rows=[(MEMBER, "optout", "unsubscribe-link:weekly-live")])
    assert wli._suppressed_for_invitation(cx2, padded, {MEMBER}) is True


def test_someone_not_paying_with_the_hub_tag_stays_blocked():
    wli = _wli()
    cx = _cx(tagged=["free@x.com"])
    assert wli._suppressed_for_invitation(cx, "free@x.com", {MEMBER}) is True


@pytest.mark.parametrize("bounce_type,source", [
    ("hard", "bounce-scan"),
    ("ghl-dnd", "people-hub"),
    ("optout", "unsubscribe-link:weekly-live"),
    ("optout", "unsubscribe-link:newsletter"),
    ("unsubscribed", "near-miss"),          # only the exact hub tag is waived
])
def test_every_table_row_blocks_a_paid_member(bounce_type, source):
    wli = _wli()
    cx = _cx(tagged=[MEMBER], rows=[(MEMBER, bounce_type, source)])
    assert wli._suppressed_for_invitation(cx, MEMBER, {MEMBER}) is True


def test_no_block_means_invited():
    wli = _wli()
    assert wli._suppressed_for_invitation(_cx(), "anyone@x.com", set()) is False


def test_both_send_paths_use_the_paid_member_check():
    """The weekly run and the correction run must both apply the same rule."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "weekly_live_invitation.py").read_text()
    body = src.split("def _suppressed_for_invitation", 1)[1].split("\ndef ", 1)[1]
    assert "email_suppression.is_suppressed(" not in body
    assert body.count("_suppressed_for_invitation(cx, ") == 3


# ── run(), the weekly send path ─────────────────────────────────────────────

KEY = "test-console-secret"
PAID_TAGGED = "paid.tagged@example.com"
FREE_TAGGED = "free.tagged@example.com"
PAID_OPTED_OUT = "paid.optout@example.com"
COACH_TAGGED = "coach.tagged@example.com"
AUDIENCE = {e: {"id": "c-" + e, "email": e, "firstName": "Pat"}
            for e in (PAID_TAGGED, FREE_TAGGED, PAID_OPTED_OUT, COACH_TAGGED)}
REASONS = {PAID_TAGGED: es.UNSUBSCRIBED_TAG, FREE_TAGGED: es.UNSUBSCRIBED_TAG,
           PAID_OPTED_OUT: "optout", COACH_TAGGED: es.UNSUBSCRIBED_TAG}


def test_the_weekly_run_mails_the_paying_member_and_no_one_blocked(monkeypatch, tmp_path):
    from dashboard import unsubscribe
    weekly = _wli()
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    monkeypatch.setenv("CONSOLE_SECRET", KEY)
    monkeypatch.setattr(weekly.appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(weekly, "_event_gate", lambda d: {"ok": True, "issues": []})
    monkeypatch.setattr(weekly, "_contacts_by_tag", lambda tag: (
        list(AUDIENCE.values()) if tag == "e4l account" else []))
    monkeypatch.setattr(weekly, "_authoritative_access_sets",
                        lambda: ({PAID_TAGGED, PAID_OPTED_OUT}, {COACH_TAGGED}))
    monkeypatch.setattr(weekly, "_find_contact", lambda email: None)
    monkeypatch.setattr(weekly, "_create_contact", lambda email, name="": None)
    monkeypatch.setattr(weekly.client_portal, "ensure_token", lambda cx, email, name: "tok")
    monkeypatch.setattr(weekly.email_suppression, "suppression_reason",
                        lambda cx, email: REASONS.get(email.strip().lower()))
    monkeypatch.setattr(weekly.time, "sleep", lambda s: None)
    sent = []

    def fake_send(contact_id, subject, text, body_html, *, email_to="", scheduled_timestamp=None):
        sent.append(email_to)
        return 201, f"msg-{len(sent)}", {}
    monkeypatch.setattr(weekly, "_send", fake_send)
    argument, _ = allowlist.encode(list(AUDIENCE), KEY)
    assert weekly.run(SimpleNamespace(date="2026-09-16", dry_run=False, send=True,
                                      only_list=argument)) == 0
    assert sorted(sent) == sorted([PAID_TAGGED, COACH_TAGGED])
