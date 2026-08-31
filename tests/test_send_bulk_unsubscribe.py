"""send_bulk's unsubscribe footer is opt-in.

Default None keeps every existing caller byte-identical, which is what stops
transactional mail (invoices, magic links, portal-ready notices) from silently
gaining an unsubscribe link.
"""
import pytest

from dashboard import inbox, unsubscribe


@pytest.fixture()
def captured(monkeypatch):
    seen = {}

    def fake_send_email(to, subject, body, from_name=None, html=None):
        seen.update(to=to, subject=subject, body=body, html=html)
        return {"via": "gmail"}

    monkeypatch.setattr(inbox, "send_email", fake_send_email)
    monkeypatch.setattr(inbox, "_is_undeliverable", lambda e: False)
    monkeypatch.delenv("BULK_VIA_GHL", raising=False)
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)
    return seen


def test_without_scope_the_body_is_untouched(captured):
    inbox.send_bulk("a@b.com", "Subj", "Hello there", html="<p>Hello there</p>")
    assert captured["body"] == "Hello there"
    assert "unsubscribe" not in (captured["html"] or "").lower()


def test_with_scope_the_footer_reaches_both_parts(captured):
    inbox.send_bulk("a@b.com", "Subj", "Hello there",
                    html="<p>Hello there</p>", unsubscribe_scope="global")
    assert "/email/unsubscribe?" in captured["body"]
    assert "/email/unsubscribe?" in captured["html"]


def test_footer_is_signed_for_the_actual_recipient(captured):
    inbox.send_bulk("who@example.com", "Subj", "Hi", unsubscribe_scope="global")
    assert unsubscribe.sign("who@example.com", "global") in captured["body"]


def test_a_text_only_send_still_gets_a_text_footer(captured):
    inbox.send_bulk("a@b.com", "Subj", "Hi", unsubscribe_scope="global")
    assert "/email/unsubscribe?" in captured["body"]
    assert captured["html"] is None
