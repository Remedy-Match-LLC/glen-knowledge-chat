"""Static contract tests for the third-party Remedy Match identity fields."""

from pathlib import Path


HTML = (Path(__file__).parents[1] / "static" / "begin-match.html").read_text()
APP = (Path(__file__).parents[1] / "app.py").read_text()


def test_someone_else_collects_separate_recipient_identity():
    assert 'id="recipient-fields" hidden' in HTML
    assert 'id="recipient-name" type="text"' in HTML
    assert 'id="recipient-email" type="email"' in HTML
    assert "subject_name: recipientName" in HTML
    assert "subject_email: recipientEmail" in HTML


def test_recipient_name_is_required_before_chat_begins():
    assert "forWhom === 'someone-else' && !recipientName" in HTML
    assert "Please add their name" in HTML


def test_recipient_email_has_reference_only_no_contact_copy():
    assert "preserve referral credit if they later create an account" in HTML
    assert "We won&rsquo;t contact them" in HTML


def test_recipient_identity_threads_current_referrer():
    assert "ref: getRefSafe()" in HTML
    assert 'for_whom == "someone-else" and subject_email and ref_slug' in APP
    assert "_capture_concierge_referral(" in APP
