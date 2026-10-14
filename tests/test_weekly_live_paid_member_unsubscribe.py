"""A paying member is still invited to the sessions they pay for (Glen, 2026-09-13).

Only the People hub's consent:unsubscribed tag is waived, and only for the paid
and certification sets. Every suppression-table row still blocks, including an
unsubscribe clicked on the invitation itself.
"""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from dashboard import email_suppression as es

MEMBER = "member@x.com"


def _wli():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from scripts import weekly_live_invitation as wli
        importlib.reload(wli)
    except Exception as e:  # noqa: BLE001 — matches the other app-importing tests
        pytest.skip(f"weekly_live_invitation not importable: {e}")
    return wli


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
    assert wli._suppressed_for_invitation(cx, " Member@X.com ", {MEMBER}) is False


def test_someone_not_paying_with_the_hub_tag_stays_blocked():
    wli = _wli()
    cx = _cx(tagged=["free@x.com"])
    assert wli._suppressed_for_invitation(cx, "free@x.com", {MEMBER}) is True


@pytest.mark.parametrize("bounce_type,source", [
    ("hard", "bounce-scan"),
    ("ghl-dnd", "people-hub"),
    ("optout", "unsubscribe-link:weekly-live"),
    ("optout", "unsubscribe-link:newsletter"),
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
