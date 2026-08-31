"""The weekly live invitation is the largest bare sender.

3,883 of the 4,660 promotional sends found in the 2026-08-30 audit came from here
with no unsubscribe link. It bypasses send_bulk and posts to the conversations API
directly, so it composes the footer itself.
"""
import datetime
import importlib
import sys
from pathlib import Path

import pytest

from dashboard import unsubscribe


def _mod(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from scripts import weekly_live_invitation as wli
        importlib.reload(wli)
    except Exception as e:  # noqa: BLE001 — matches the other app-importing tests
        pytest.skip(f"weekly_live_invitation not importable: {e}")
    return wli


def _call(monkeypatch, email):
    wli = _mod(monkeypatch)
    return wli._copy("Sam", "https://portal.example/x", True,
                     datetime.date(2026, 9, 2), email)


def test_text_part_carries_an_unsubscribe_link(monkeypatch):
    text, _ = _call(monkeypatch, "a@b.com")
    assert "/email/unsubscribe?" in text


def test_html_part_carries_an_unsubscribe_link(monkeypatch):
    _, body_html = _call(monkeypatch, "a@b.com")
    assert "/email/unsubscribe?" in body_html


def test_link_is_signed_for_that_recipient(monkeypatch):
    text, _ = _call(monkeypatch, "who@example.com")
    assert unsubscribe.sign("who@example.com", "weekly-live") in text


def test_footer_anchor_survives_the_body_escaping(monkeypatch):
    # The body is html.escape()d; the footer must be appended after that or its
    # anchor arrives as visible &lt;a&gt; text instead of a clickable link.
    _, body_html = _call(monkeypatch, "a@b.com")
    assert '<a href="' in body_html


def test_sends_from_the_consolidated_identity(monkeypatch):
    # Not just the right domain: the same From address the vault content sender
    # and the app use, so Gmail builds one reputation instead of three.
    wli = _mod(monkeypatch)
    assert wli.FROM_ADDRESS == "Dr. Glen Swartwout <drglen@mail.remedymatch.com>"
