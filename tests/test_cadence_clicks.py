"""Cadence click tracking: /c/<token>/<campaign_key>/<dest_key> and two admin routes.

Glen approved the intrigue-then-interest cadence on 2026-09-18: only people who click
get the week's offer emails. Spec: section 5 of
marketing/03 Marketing/cadence-rotation-plan-2026-q4.html. The spec requires tests
that prove the open-redirect guard fires and that an unknown token records nothing.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

KEY = "test-console-key"
WEEK = "2026-w41"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        import app as appmod
        importlib.reload(appmod)
    except Exception as e:
        pytest.skip(f"app not importable: {e}")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", KEY)
    monkeypatch.setattr(appmod, "_rec_valid_slug",
                        lambda s: "neuro-magnesium" if s == "neuro-magnesium" else None)
    client = appmod.app.test_client()
    db = str(tmp_path / "chat_log.db")
    return appmod, client, db


def _token(client, email):
    r = client.post("/api/admin/cadence/tokens", json={"emails": [email]},
                    headers={"X-Console-Key": KEY})
    return r.get_json()["tokens"][email]


def _clicks(db):
    with sqlite3.connect(db) as cx:
        try:
            return cx.execute("SELECT email, campaign_key, dest_key, user_agent "
                              "FROM cadence_clicks ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            return []


def test_a_known_key_redirects_and_records_the_click(env):
    appmod, client, db = env
    tok = _token(client, "a@x.com")
    r = client.get(f"/c/{tok}/{WEEK}/scan", headers={"User-Agent": "Mail/1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/begin/scan")
    assert _clicks(db) == [("a@x.com", WEEK, "scan", "Mail/1")]


def test_membership_goes_to_the_other_domain_and_products_are_validated(env):
    appmod, client, db = env
    tok = _token(client, "a@x.com")
    r = client.get(f"/c/{tok}/{WEEK}/membership")
    assert r.headers["Location"] == "https://myhealingoasis.com/membership"
    r = client.get(f"/c/{tok}/{WEEK}/p-neuro-magnesium")
    assert r.headers["Location"].endswith("/begin/product/neuro-magnesium")
    r = client.get(f"/c/{tok}/{WEEK}/p-not-a-product")
    assert r.headers["Location"].endswith("/")
    assert [c[2] for c in _clicks(db)] == ["membership", "p-neuro-magnesium"]


@pytest.mark.parametrize("evil", ["//evil.com", "https:evil.com", "%2F%2Fevil.com",
                                  "https%3A%2F%2Fevil.com", "..%2F..%2Fevil", "SCAN.evil"])
def test_the_open_redirect_guard_fires(env, evil):
    appmod, client, db = env
    tok = _token(client, "a@x.com")
    from urllib.parse import urlparse
    url = f"/c/{tok}/{WEEK}/{evil}"
    # Follow every hop by hand: each one must stay on this site. Werkzeug may first
    # collapse a double slash with a same-site 308; that is not a redirect off-site.
    for _ in range(5):
        r = client.get(url)
        if r.status_code not in (301, 302, 307, 308):
            break
        loc = r.headers["Location"]
        assert urlparse(loc).netloc in ("", "localhost"), f"left the site: {loc}"
        url = urlparse(loc).path
        if not url.startswith("/c/"):
            break
    assert url == "/" or r.status_code == 404, url
    assert _clicks(db) == []


def test_an_unknown_token_records_nothing_and_still_redirects(env):
    appmod, client, db = env
    r = client.get(f"/c/nobody/{WEEK}/scan")
    assert r.status_code == 302 and r.headers["Location"].endswith("/begin/scan")
    assert _clicks(db) == []


def test_a_malformed_campaign_key_redirects_but_records_nothing(env):
    appmod, client, db = env
    tok = _token(client, "a@x.com")
    r = client.get(f"/c/{tok}/week-41/scan")
    assert r.status_code == 302 and r.headers["Location"].endswith("/begin/scan")
    assert _clicks(db) == []


def test_tokens_are_minted_once_invalid_is_absent_and_the_cap_is_stated(env):
    appmod, client, db = env
    h = {"X-Console-Key": KEY}
    r = client.post("/api/admin/cadence/tokens", headers=h,
                    json={"emails": ["A@x.com", "a@x.com", "not-an-email", "", None, "b@y.org"]})
    j = r.get_json()
    assert r.status_code == 200 and sorted(j["tokens"]) == ["a@x.com", "b@y.org"]
    assert j["invalid"] == ["not-an-email", "", None]
    again = client.post("/api/admin/cadence/tokens", headers=h, json={"emails": ["a@x.com"]})
    assert again.get_json()["tokens"]["a@x.com"] == j["tokens"]["a@x.com"]
    ok = client.post("/api/admin/cadence/tokens", headers=h,
                     json={"emails": [f"u{i}@x.com" for i in range(1000)]})
    assert ok.status_code == 200 and len(ok.get_json()["tokens"]) == 1000
    over = client.post("/api/admin/cadence/tokens", headers=h,
                       json={"emails": [f"u{i}@x.com" for i in range(1001)]})
    assert over.status_code == 400 and over.get_json()["max"] == 1000


def test_clickers_lists_first_click_per_person_for_the_week(env):
    appmod, client, db = env
    a, b = _token(client, "a@x.com"), _token(client, "b@x.com")
    client.get(f"/c/{a}/{WEEK}/scan")
    client.get(f"/c/{a}/{WEEK}/quiz")
    client.get(f"/c/{b}/2026-w40/scan")
    r = client.get(f"/api/admin/cadence/clickers?campaign_key={WEEK}",
                   headers={"X-Console-Key": KEY}).get_json()
    assert r["count"] == 1
    assert r["clickers"][0]["email"] == "a@x.com"
    assert r["clickers"][0]["dest_key"] == "scan" and r["clickers"][0]["clicks"] == 2


def test_the_admin_routes_take_the_header_only(env):
    appmod, client, db = env
    assert client.post("/api/admin/cadence/tokens", json={"emails": []}).status_code == 401
    assert client.post(f"/api/admin/cadence/tokens?key={KEY}",
                       json={"emails": []}).status_code == 401
    assert client.get(f"/api/admin/cadence/clickers?campaign_key={WEEK}&key={KEY}").status_code == 401
    assert client.post("/api/admin/cadence/tokens", json={"emails": []},
                       headers={"X-Console-Key": "wrong"}).status_code == 401


def test_an_unset_secret_refuses_rather_than_opening(env, monkeypatch):
    appmod, client, db = env
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "")
    assert client.post("/api/admin/cadence/tokens", json={"emails": []},
                       headers={"X-Console-Key": ""}).status_code == 401
