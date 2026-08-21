import os
import sqlite3

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "test")

import app as appmod
from dashboard import client_scans, e4l_account_notifications


def _db(tmp_path, monkeypatch):
    path = tmp_path / "match-e4l.db"
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    with sqlite3.connect(path) as cx:
        client_scans.init_client_scans_table(cx)
    return path


def test_existing_e4l_account_goes_to_portal(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch)
    with sqlite3.connect(path) as cx:
        e4l_account_notifications.init_table(cx)
        cx.execute(
            "INSERT INTO e4l_accounts VALUES (?,?,?,?,?,?)",
            ("member@example.com", "Member", "", "msg-1", "", ""),
        )
        cx.commit()
    monkeypatch.setattr(
        appmod, "get_authenticated_user",
        lambda request: {"email": "MEMBER@example.com"},
    )

    response = appmod.app.test_client().get("/begin/match/e4l-link?ref=partner")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://portal.e4l.com"
    assert response.headers["Cache-Control"] == "no-store"


def test_unknown_or_logged_out_visitor_keeps_signup_link(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "get_authenticated_user", lambda request: None)

    response = appmod.app.test_client().get("/begin/match/e4l-link?ref=partner name")

    assert response.status_code == 302
    assert response.headers["Location"] == (
        "https://truly.vip/E4L?utm_source=partner+name"
        "&utm_medium=affiliate&utm_campaign=begin-match-e4l"
    )


def test_match_page_uses_server_side_e4l_resolver():
    html = (appmod.STATIC / "begin-match.html").read_text()
    assert "BASE + '/begin/match/e4l-link?ref=' + slug" in html
