"""Unsubscribe footer rendering, in both the text and HTML parts."""
import pytest

from dashboard import unsubscribe


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def test_text_footer_contains_a_working_url():
    out = unsubscribe.footer_text("a@b.com")
    assert unsubscribe.unsubscribe_url("a@b.com") in out


def test_html_footer_is_an_anchor():
    out = unsubscribe.footer_html("a@b.com")
    assert '<a href="' in out and "Unsubscribe" in out


def test_html_footer_escapes_the_address():
    out = unsubscribe.footer_html('x"><script>@b.com')
    assert "<script>" not in out


def test_footer_identifies_the_sender():
    # CAN-SPAM requires a physical postal address in commercial mail.
    assert "Hawaii" in unsubscribe.footer_text("a@b.com")
    assert "Hawaii" in unsubscribe.footer_html("a@b.com")


def test_scope_flows_into_the_link():
    out = unsubscribe.footer_text("a@b.com", "nurture")
    assert "scope=nurture" in out
