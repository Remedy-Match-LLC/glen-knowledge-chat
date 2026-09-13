"""The membership lookup: why does the pricing gate think this email is a member?

Added 2026-09-12. The members board and the pricing gate disagreed in production
and nothing could show why, so a genuine grant and a pricing leak looked identical
from outside. They need opposite fixes, so guessing was not acceptable.
"""
import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    import app as appmod
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(appmod.dashboard, "CONSOLE_SECRET", "test-secret")
    return appmod, appmod.app.test_client()


def test_lookup_requires_the_console_key(client):
    _, c = client
    assert c.get("/api/console/membership-lookup?email=a@b.com").status_code == 401


def test_lookup_requires_an_email(client):
    _, c = client
    r = c.get("/api/console/membership-lookup", headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 400


def test_lookup_reports_the_disagreement_that_prompted_it(client, monkeypatch):
    """The whole point. A member by the pricing gate who the board cannot see must
    come back with disagreement=True, not merely with is_paid_member=True."""
    appmod, c = client
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: True)
    monkeypatch.setattr(appmod, "membership_category", lambda e: "full")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: {"source": "x"})
    from dashboard import subscriptions as _subs
    monkeypatch.setattr(_subs, "list_active_memberships", lambda cx: [])
    monkeypatch.setattr(_subs, "list_membership_holders", lambda cx: [])

    d = c.get("/api/console/membership-lookup?email=Ghost@Example.com",
              headers={"X-Console-Key": "test-secret"}).get_json()
    assert d["email"] == "ghost@example.com", "email must be normalised before lookup"
    assert d["is_paid_member"] is True
    assert d["board_shows"] is False
    assert d["disagreement"] is True
    assert d["board_via"] is None


def test_agreement_is_not_reported_as_a_disagreement(client, monkeypatch):
    """The negative case. Without this, a check that always says 'disagreement'
    would pass the test above and tell us nothing."""
    appmod, c = client
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: True)
    monkeypatch.setattr(appmod, "membership_category", lambda e: "full")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: {"source": "x"})
    from dashboard import subscriptions as _subs
    monkeypatch.setattr(_subs, "list_active_memberships",
                        lambda cx: [{"email": "real@example.com"}])
    monkeypatch.setattr(_subs, "list_membership_holders", lambda cx: [])

    d = c.get("/api/console/membership-lookup?email=real@example.com",
              headers={"X-Console-Key": "test-secret"}).get_json()
    assert d["board_shows"] is True
    assert d["board_via"] == "subscription"
    assert d["disagreement"] is False


def test_a_grant_only_holder_is_credited_to_the_grant(client, monkeypatch):
    appmod, c = client
    monkeypatch.setattr(appmod, "_is_paid_member", lambda e: True)
    monkeypatch.setattr(appmod, "membership_category", lambda e: "full")
    monkeypatch.setattr(appmod, "_active_membership_for_email", lambda e: {"source": "biofield"})
    from dashboard import subscriptions as _subs
    monkeypatch.setattr(_subs, "list_active_memberships", lambda cx: [])
    monkeypatch.setattr(_subs, "list_membership_holders",
                        lambda cx: [{"email": "granted@example.com"}])

    d = c.get("/api/console/membership-lookup?email=granted@example.com",
              headers={"X-Console-Key": "test-secret"}).get_json()
    assert d["board_via"] == "grant"
    assert d["disagreement"] is False
