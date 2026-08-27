# tests/test_practitioner_site_routes.py
"""Routes for the practitioner site at myhealingoasis.com/<slug>."""
import os
import pathlib
import sqlite3

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import practitioner_slugs as ps

PORTAL_HOST = "myhealingoasis.com"
FUNNEL_HOST = "illtowell.com"


def _seed(db_path):
    cx = sqlite3.connect(db_path)
    cx.execute("CREATE TABLE IF NOT EXISTS affiliate_signups ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,"
               " name TEXT, email TEXT, organization TEXT DEFAULT '',"
               " slug TEXT NOT NULL UNIQUE, token TEXT, status TEXT)")
    cx.execute("INSERT INTO affiliate_signups"
               " (created_at,name,email,organization,slug,token,status)"
               " VALUES ('2026-08-27','Mary Boyd','m@x.com','Boyd Coaching',"
               "'mary-boyd','tok1','approved')")
    ps.init_tables(cx)
    cx.commit()
    cx.close()


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "1")
    monkeypatch.setenv("PORTAL_BASE_URL", f"https://{PORTAL_HOST}")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_canonical_slug_serves_on_portal_host(client):
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 200
    assert b"<html" in r.data.lower()


def test_canonical_slug_is_still_noindex(client):
    """Lifting noindex is section 5, not this work."""
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.headers.get("X-Robots-Tag") == "noindex"


def test_canonical_slug_sets_attribution_cookie(client):
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert "rm_ref=mary-boyd" in r.headers.get("Set-Cookie", "")


def test_slug_404s_on_the_funnel_host(client):
    """The catch-all must not exist on illtowell.com at all."""
    assert client.get("/mary-boyd", base_url=f"http://{FUNNEL_HOST}").status_code == 404


def test_unknown_slug_404s(client):
    assert client.get("/nobody-here", base_url=f"http://{PORTAL_HOST}").status_code == 404


def test_malformed_slug_404s(client):
    assert client.get("/Bad--Shape", base_url=f"http://{PORTAL_HOST}").status_code == 404


def test_slug_404s_when_public_surface_flag_off(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "")
    assert client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}").status_code == 404


def test_named_route_still_wins_over_the_catch_all(client):
    """A static rule must beat the catch-all even on the portal host."""
    endpoint = appmod.app.url_map.bind(PORTAL_HOST).match("/sample")[0]
    assert endpoint != "practitioner_site"


def _claim(db_path, alias, canonical="mary-boyd"):
    cx = sqlite3.connect(db_path)
    ps.claim_alias(cx, canonical, alias, frozenset())
    cx.close()


def test_alias_301s_to_canonical(client, tmp_path):
    _claim(appmod.LOG_DB, "boyd-coaching")
    r = client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/mary-boyd")


def test_alias_does_not_serve_content(client):
    """Alternates redirect; they never render the storefront, so they cannot
    compete with the canonical URL as duplicate content.

    Note the assertion is against STOREFRONT markers, not against "<html".
    Werkzeug's 301 always emits a small "Redirecting..." HTML stub, which is
    correct and conventional -- crawlers follow the redirect and never index
    the stub. What must never appear here is the practitioner's actual page.
    """
    _claim(appmod.LOG_DB, "boyd-coaching")
    r = client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert b'id="bio"' not in r.data
    assert b"Browse the full catalog" not in r.data
    assert b"Redirecting" in r.data


def test_legacy_p_slug_301s_to_canonical_on_portal_host(client):
    r = client.get("/p/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/mary-boyd")


def test_legacy_p_slug_still_serves_on_the_funnel_host(client):
    """Old links must never break. /p/<slug> on illtowell.com is untouched."""
    r = client.get("/p/mary-boyd", base_url=f"http://{FUNNEL_HOST}")
    assert r.status_code == 200
    assert r.headers.get("X-Robots-Tag") == "noindex"


def test_alias_301_deliberately_carries_no_noindex(client):
    """An alias 301 must NOT set X-Robots-Tag: noindex.

    This is deliberate, not an oversight. noindex on a redirect is an SEO
    anti-pattern: it can cause the redirect TARGET to be treated as noindex,
    which would defeat the whole point of an alternate passing its authority
    to the canonical URL. The canonical response carries noindex; the hop
    to it must not.
    """
    _claim(appmod.LOG_DB, "boyd-coaching")
    r = client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers.get("X-Robots-Tag") is None


def test_alias_redirect_sets_cookie_to_canonical_not_alias(client):
    """The cookie test elsewhere only covers requesting the canonical slug
    directly, so it cannot tell 'cookie set to raw slug' apart from 'cookie
    set to canonical'. Follow the alias redirect and check the final cookie."""
    _claim(appmod.LOG_DB, "boyd-coaching")
    r = client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}",
                    follow_redirects=True)
    assert r.status_code == 200
    assert "rm_ref=mary-boyd" in r.headers.get("Set-Cookie", "")


def test_settings_page_shows_the_current_storefront_url():
    """The settings copy must not advertise the retired illtowell.com/p/ form."""
    html = pathlib.Path(
        appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    assert "illtowell.com/p/" not in html
    assert "myhealingoasis.com/" in html
