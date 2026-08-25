import sqlite3
from urllib.parse import parse_qs, urlparse


def _person(cx, email="provider@example.com"):
    from dashboard import portal_identity as pi
    pi._ensure_people_table(cx)
    cx.execute("INSERT INTO people (email,name,roles,created_at,updated_at) VALUES (?,?,?,?,?)",
               (email, "Provider Client", '["client"]', "t", "t"))
    cx.commit()
    return cx.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()[0]


def test_oauth_transaction_is_provider_bound_and_one_time(tmp_path):
    from dashboard import portal_providers as pp
    cx = sqlite3.connect(str(tmp_path / "provider.db"))
    state, nonce, verifier = pp.create_transaction(
        cx, "google", "https://myhealingoasis.com/portal/auth/google/callback")
    assert pp.consume_transaction(cx, state, "apple") is None
    transaction = pp.consume_transaction(cx, state, "google")
    assert transaction["nonce"] == nonce
    assert transaction["verifier"] == verifier
    assert pp.consume_transaction(cx, state, "google") is None


def test_google_authorization_url_has_oidc_pkce_and_exact_callback():
    from dashboard import portal_providers as pp
    callback = "https://myhealingoasis.com/portal/auth/google/callback"
    url = pp.google_authorization_url("client", callback, "state", "nonce", "verifier")
    query = parse_qs(urlparse(url).query)
    assert query["redirect_uri"] == [callback]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["nonce"] == ["nonce"]


def test_apple_authorization_url_uses_form_post():
    from dashboard import portal_providers as pp
    callback = "https://myhealingoasis.com/portal/auth/apple/callback"
    url = pp.apple_authorization_url("service.id", callback, "state", "nonce")
    query = parse_qs(urlparse(url).query)
    assert query["redirect_uri"] == [callback]
    assert query["response_mode"] == ["form_post"]
    assert query["scope"] == ["name email"]


def test_provider_link_requires_confirmation_and_is_one_time(tmp_path):
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(str(tmp_path / "provider.db"))
    pid = _person(cx)
    assert pa.identity_by_provider_subject(cx, "google", "subject-1") is None
    token = pa.create_provider_link_confirmation(
        cx, pid, "google", "subject-1", "provider@example.com", "Provider Client")
    assert pa.validate_provider_link_confirmation(cx, token)["person_id"] == pid
    assert pa.consume_provider_link_confirmation(cx, token) == pid
    assert pa.consume_provider_link_confirmation(cx, token) is None
    assert pa.identity_by_provider_subject(cx, "google", "subject-1") == pid


def test_provider_subject_cannot_be_linked_to_two_people(tmp_path):
    from dashboard import portal_auth as pa
    cx = sqlite3.connect(str(tmp_path / "provider.db"))
    first = _person(cx, "first@example.com")
    second = _person(cx, "second@example.com")
    pa.link_external_identity(cx, first, "google", "same-subject", "first@example.com")
    import pytest
    with pytest.raises(ValueError):
        pa.link_external_identity(cx, second, "google", "same-subject", "second@example.com")


def test_verified_claim_parser_rejects_false_string():
    from dashboard.portal_providers import _claim_true
    assert _claim_true(True)
    assert _claim_true("true")
    assert not _claim_true(False)
    assert not _claim_true("false")
