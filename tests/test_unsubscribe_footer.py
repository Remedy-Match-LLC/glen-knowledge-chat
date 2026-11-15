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
    # CAN-SPAM requires a valid physical postal address in commercial mail. A city alone
    # is not one. Glen chose the street address on 2026-11-12 (marketing tab).
    street = "Remedy Match LLC, 351 Wailuku Drive, Hilo, Hawai'i 96720 USA"
    assert unsubscribe.POSTAL == street
    assert unsubscribe.footer_text("a@b.com").endswith("\n" + street)
    # The HTML part escapes the apostrophe; a reader still sees Hawai'i.
    html = unsubscribe.footer_html("a@b.com")
    assert "Remedy Match LLC, 351 Wailuku Drive, Hilo, Hawai&#x27;i 96720 USA</div>" in html


def test_scope_flows_into_the_link():
    out = unsubscribe.footer_text("a@b.com", "nurture")
    assert "scope=nurture" in out
