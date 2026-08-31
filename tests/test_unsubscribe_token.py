"""Signed unsubscribe tokens.

The signature is what stands between a link in an email and anyone being able to
opt out anyone else by editing a query string.
"""
import pytest

from dashboard import unsubscribe


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(unsubscribe, "_SECRET", "test-secret-abc", raising=False)


def test_sign_is_stable_and_case_insensitive():
    assert unsubscribe.sign("A@B.com", "global") == unsubscribe.sign("a@b.com  ", "global")


def test_verify_accepts_its_own_signature():
    sig = unsubscribe.sign("a@b.com", "global")
    assert unsubscribe.verify("a@b.com", "global", sig) is True


def test_verify_rejects_a_signature_from_another_scope():
    sig = unsubscribe.sign("a@b.com", "nurture")
    assert unsubscribe.verify("a@b.com", "global", sig) is False


def test_verify_rejects_a_signature_for_another_address():
    sig = unsubscribe.sign("a@b.com", "global")
    assert unsubscribe.verify("c@d.com", "global", sig) is False


def test_verify_rejects_empty_signature():
    assert unsubscribe.verify("a@b.com", "global", "") is False


def test_url_carries_scope_and_signature():
    url = unsubscribe.unsubscribe_url("a@b.com", "global")
    assert "/email/unsubscribe?" in url
    assert "scope=global" in url
    assert unsubscribe.sign("a@b.com", "global") in url
