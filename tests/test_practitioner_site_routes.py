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
    # setitem, not a bare assignment: app is a module-level singleton shared by
    # every test in the process, and an unrestored TESTING=True leaks into
    # files that run after this one.
    monkeypatch.setitem(appmod.app.config, "TESTING", True)
    return appmod.app.test_client()


def test_canonical_slug_serves_on_portal_host(client):
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 200
    assert b"<html" in r.data.lower()


def test_capitalized_slug_301s_to_the_lowercase_canonical(client):
    """A capitalized URL must NOT serve content.

    practitioner_site normalizes before the lookup, so /Mary-Boyd used to
    resolve and serve a 200. But practitioner-storefront.html re-derives its
    own fetch key from location.pathname, and affiliate_signups.slug has no
    COLLATE NOCASE, so /api/p/Mary-Boyd 404s and the page renders blank.
    Redirecting to the normalized form keeps the URL and the page's own key
    identical, and gives one canonical URL per practitioner.
    """
    r = client.get("/Mary-Boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/mary-boyd")


def test_all_caps_slug_301s_to_the_lowercase_canonical(client):
    r = client.get("/MARY-BOYD", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/mary-boyd")


def test_lowercase_slug_serves_directly_with_no_redirect_hop(client):
    """The normalization redirect must not add a hop to the common case."""
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 200
    assert r.headers.get("Location") is None


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


def test_legacy_p_slug_302s_to_canonical_on_portal_host(client):
    """302, NOT 301 -- deliberate, do not "fix" this back without reading why.

    A 301 is cached by browsers indefinitely, so a wrong redirect could not be
    rolled back by reverting the deploy. Nothing on this surface is indexable
    yet (X-Robots-Tag: noindex; lifting it is spec section 5), so there is no
    link authority to preserve in the meantime. Promote to 301 once the
    production roster audit has run and section 5 makes these pages indexable.
    """
    r = client.get("/p/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/mary-boyd")


def test_alias_hop_inside_the_site_route_stays_a_permanent_301(client):
    """The 302 above applies ONLY to the legacy /p/<slug> hop. An alternate ->
    canonical redirect is a permanent statement about the namespace, and it is
    what passes an alternate's authority to the canonical URL."""
    _claim(appmod.LOG_DB, "boyd-coaching")
    assert client.get("/boyd-coaching",
                      base_url=f"http://{PORTAL_HOST}").status_code == 301


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


def test_db_failure_404s_rather_than_500ing_the_whole_catch_all(client, monkeypatch, tmp_path):
    """Fail closed, the convention public_surface.build_share_header documents.

    This is a ROOT-LEVEL catch-all on a public, unauthenticated surface: it is
    reached by every bot probe of /admin, /wordpress, /.env. If a missing or
    broken affiliate_signups table let the exception out, every one of those
    would become a 500 instead of a 404 -- turning one schema problem into a
    site-wide error signal.
    """
    empty = str(tmp_path / "no-tables.db")
    sqlite3.connect(empty).close()          # a real DB with no affiliate_signups
    monkeypatch.setattr(appmod, "LOG_DB", empty)
    assert client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}").status_code == 404


def _views(db_path):
    cx = sqlite3.connect(db_path)
    try:
        return cx.execute(
            "SELECT slug, surface FROM public_surface_views ORDER BY id").fetchall()
    finally:
        cx.close()


def test_serving_a_canonical_slug_records_a_view(client):
    """public_surface.record_view's own docstring says per-slug view counts are
    the instrumentation this feature is measured by. /p/<slug> now redirects
    BEFORE its record_view call, so without this the metric drops to zero on
    the portal host exactly as traffic moves there."""
    client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert _views(appmod.LOG_DB) == [("mary-boyd", "storefront")]


def test_an_alias_records_the_view_under_the_canonical_slug(client):
    """Views must aggregate on one slug per practitioner, not split across
    however many alternates they publish."""
    _claim(appmod.LOG_DB, "boyd-coaching")
    client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}",
               follow_redirects=True)
    assert _views(appmod.LOG_DB) == [("mary-boyd", "storefront")]


def test_view_recording_can_never_break_the_page(client, monkeypatch):
    """Instrumentation is best-effort. Mirror the guard in the /p/<slug>
    handler: if record_view raises, the practitioner's page still serves."""
    from dashboard import public_surface as _psurf

    def _boom(*a, **k):
        raise RuntimeError("instrumentation is down")

    monkeypatch.setattr(_psurf, "record_view", _boom)
    r = client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 200


@pytest.mark.parametrize("path", [
    "/chat", "/full-report", "/rate", "/generate-audio", "/ingest-transcript",
    "/transcribe",
])
def test_get_on_a_post_only_root_route_404s_not_405s(client, path):
    """Behaviour PIN, not an endorsement. Before the /<slug> catch-all these
    returned 405 with 'Allow: POST, OPTIONS'; they now return 404.

    Werkzeug's StateMachineMatcher, when a static rule matches the path but not
    the method, records the allowed methods and then CONTINUES on to the
    dynamic transitions -- so /<slug> matches, practitioner_site runs, and its
    own 404 is what the client sees. Note this happens on the FUNNEL host too:
    the rule is registered globally and host-gated inside the view, so the
    matcher reaches it regardless of host.

    Benign today (these are internal POST endpoints, and 404 leaks less than
    405 does). Pinned so that if a future change moves it back to 405, or moves
    it somewhere else again, somebody notices deliberately.
    """
    r = client.get(path, base_url=f"http://{FUNNEL_HOST}")
    assert r.status_code == 404
    assert r.headers.get("Allow") is None
