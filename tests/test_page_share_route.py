import sqlite3

import pytest


@pytest.fixture
def share_client(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    import app as appmod
    from dashboard import portal_identity as pi

    db_path = str(tmp_path / "share.db")
    monkeypatch.setattr(appmod, "LOG_DB", db_path)
    monkeypatch.setattr(appmod, "PUBLIC_BASE_URL", "https://illtowell.com")
    monkeypatch.setattr(appmod, "_page_referral_sharing_enabled", lambda: True)
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    appmod.app.config["TESTING"] = True

    with sqlite3.connect(db_path) as cx:
        pi._ensure_people_table(cx)
        cx.execute(
            "CREATE TABLE affiliate_signups ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL, "
            "email TEXT NOT NULL UNIQUE, organization TEXT DEFAULT '', website TEXT DEFAULT '', "
            "promo_method TEXT DEFAULT '', slug TEXT NOT NULL UNIQUE, token TEXT NOT NULL UNIQUE, "
            "status TEXT DEFAULT 'approved', notes TEXT DEFAULT '', referred_by TEXT DEFAULT '', "
            "short_url TEXT DEFAULT '', gifting_activated_at TEXT)"
        )
        cx.execute(
            "CREATE TABLE referral_sources ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL, "
            "slug TEXT NOT NULL UNIQUE, description TEXT DEFAULT '', utm_source TEXT NOT NULL, "
            "utm_medium TEXT DEFAULT 'referral', utm_campaign TEXT DEFAULT '', active INTEGER DEFAULT 1)"
        )
        cx.execute(
            "INSERT INTO people (email,name,roles,created_at,updated_at) VALUES (?,?,?,?,?)",
            ("jane@example.com", "Jane Doe", '["client"]', "t", "t"),
        )
        person_id = cx.execute(
            "SELECT id FROM people WHERE email='jane@example.com'"
        ).fetchone()[0]
        session = pi.create_client_session(cx, person_id, "jane@example.com")

    client = appmod.app.test_client()
    return client, appmod, session


def test_signed_in_client_gets_personal_page_link(share_client):
    client, _, session = share_client
    client.set_cookie("rm_portal_session", session)
    response = client.get("/api/page-share-link?path=/begin/explore")
    assert response.status_code == 200
    body = response.get_json()
    assert body["title"] == "Explore Remedy Match"
    assert body["share_url"] == (
        "https://illtowell.com/begin/explore?ref=jane-doe&src=share"
    )
    assert "may receive rewards" in body["disclosure"]


def test_anonymous_visitor_cannot_mint_link(share_client):
    client, _, _ = share_client
    response = client.get("/api/page-share-link?path=/begin/explore")
    assert response.status_code == 401


def test_private_page_fails_closed(share_client):
    client, _, session = share_client
    client.set_cookie("rm_portal_session", session)
    response = client.get("/api/page-share-link?path=/portal/private-token")
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_shareable"


def test_feature_flag_keeps_route_dark(share_client, monkeypatch):
    client, appmod, session = share_client
    monkeypatch.setattr(appmod, "_page_referral_sharing_enabled", lambda: False)
    client.set_cookie("rm_portal_session", session)
    assert client.get("/api/page-share-link?path=/begin/explore").status_code == 404


def test_first_slice_pages_load_share_component():
    from pathlib import Path

    static = Path(__file__).parents[1] / "static"
    for name in ("begin-product.html", "begin-explore.html", "begin-tools.html"):
        assert '/static/page-share.js' in (static / name).read_text(encoding="utf-8")
