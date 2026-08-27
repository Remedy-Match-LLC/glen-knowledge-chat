# tests/test_practitioner_site_routes.py
"""Routes for the practitioner site at myhealingoasis.com/<slug>."""
import os
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
