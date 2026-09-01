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


def test_db_failure_does_not_claim_the_practitioner_is_gone(client, monkeypatch, tmp_path):
    """INVERTED 2026-08-29 on Glen's ruling. This test used to assert 404.

    Its original reasoning: this is a ROOT-LEVEL catch-all on a public surface,
    reached by every bot probe of /admin, /wordpress, /.env, so letting an
    exception out would turn one schema problem into a site-wide 500.

    The ruling overrides it. "This practitioner does not exist" is not an
    acceptable answer when the app could not look. A client following a
    referral link, and a crawler, cannot distinguish that 404 from the person
    having been removed -- and /<slug> is the route the section 5b sitemap
    will list, so a false 404 here eventually costs a real page its indexing.

    The blast radius was also smaller than the original reasoning assumed:
    check_shape rejects /.env, /wp-login.php, /xmlrpc.php and /.git/config
    before any query runs, so what actually changes is a 500 on word-shaped
    probes like /admin, during an outage in which nothing else works either.

    The distinction the route draws now is "I looked and it is not there"
    (still 404 -- see test_a_genuinely_unknown_slug_is_still_404 in
    tests/test_practitioner_site_render.py) versus "I could not look" (500).
    """
    empty = str(tmp_path / "no-tables.db")
    sqlite3.connect(empty).close()          # a real DB with no affiliate_signups
    monkeypatch.setattr(appmod, "LOG_DB", empty)
    # This file's client propagates rather than converting to a 500 response,
    # so observe the exception directly -- the same technique
    # test_storefront_deliberately_500s_on_corrupt_database uses for /p/<slug>
    # in tests/test_public_surface_attribution.py. In production, where the app
    # is not in testing mode, this is the 500 the route returns.
    with pytest.raises(sqlite3.OperationalError):
        client.get("/mary-boyd", base_url=f"http://{PORTAL_HOST}")


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


# --- page_slug: the practitioner-chosen public URL --------------------------
# Glen's affiliate slug is his COMPANY name, `remedy-match`, and it is the
# attribution key carried on stored lead rows, on a printed shortlink and in
# 90-day referral cookies -- so it can never be renamed. His page belongs at
# /dr-glen. These pin that both names reach the same page, that only one of
# them serves content, and that everything attribution-shaped stays on the
# affiliate slug.

def _seed_page_slug(db_path, slug="remedy-match", page="dr-glen",
                    name="Glen Swartwout", email="g@example.com",
                    organization="Remedy Match"):
    """Add an approved practitioner whose page slug differs from her/his
    affiliate slug. Written through ps.set_page_slug rather than a raw UPDATE,
    so the test exercises the same writer the settings form will."""
    cx = sqlite3.connect(db_path)
    cx.execute("INSERT INTO affiliate_signups"
               " (created_at,name,email,organization,slug,token,status)"
               " VALUES ('2026-08-27',?,?,?,?,'tok2','approved')",
               (name, email, organization, slug))
    cx.commit()
    ps.set_page_slug(cx, slug, page, reserved=frozenset())
    cx.close()


def test_the_public_page_serves_the_canonical_page_slug(client):
    _seed_page_slug(appmod.LOG_DB)
    r = client.get("/dr-glen", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<h1>Glen Swartwout</h1>" in body
    # The canonical tag and the JSON-LD `url` must agree with the address bar.
    # A canonical that disagrees with the URL it is served at splits the same
    # person's link authority across two URLs, which is the whole reason
    # _render_practitioner_page takes a canonical_slug argument at all.
    assert f'<link rel="canonical" href="https://{PORTAL_HOST}/dr-glen">' in body
    # Strongest available assertion on the JSON-LD `url` and every other
    # URL-shaped string in the document at once: the affiliate slug appears
    # nowhere in the BODY. It still belongs in the rm_ref header, which this
    # does not touch.
    assert "remedy-match" not in body


def test_the_public_page_302s_the_legacy_affiliate_slug(client):
    """302, NOT 301. page_slug is practitioner-changeable, and a 301 is cached
    by browsers indefinitely -- so a later rename would strand everyone who
    ever visited under the previous name."""
    _seed_page_slug(appmod.LOG_DB)
    r = client.get("/remedy-match", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/dr-glen")


def test_the_legacy_affiliate_slug_does_not_serve_content(client):
    """Two URLs serving the same practitioner is the duplication this feature
    exists to collapse. The legacy name redirects; it never renders."""
    _seed_page_slug(appmod.LOG_DB)
    r = client.get("/remedy-match", base_url=f"http://{PORTAL_HOST}")
    assert b"<h1>Glen Swartwout</h1>" not in r.data
    assert b"Redirecting" in r.data


def test_case_normalisation_stays_a_301(client):
    """Deterministic and unchangeable, unlike a page_slug, so it keeps the
    permanent redirect it has always had."""
    _seed_page_slug(appmod.LOG_DB)
    r = client.get("/Dr-Glen", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/dr-glen")
    # And the target it names actually serves. Without this the assertions
    # above pass on today's code, where /Dr-Glen 301s to a /dr-glen that 404s
    # -- a redirect to nowhere is not case normalisation working.
    followed = client.get("/Dr-Glen", base_url=f"http://{PORTAL_HOST}",
                          follow_redirects=True)
    assert followed.status_code == 200
    assert "<h1>Glen Swartwout</h1>" in followed.get_data(as_text=True)


def test_a_mixed_case_legacy_slug_lands_on_the_canonical_without_a_loop(client):
    """The interaction between the two redirects. Case normalisation must run
    FIRST: /Remedy-Match 301s to /remedy-match, which 302s to /dr-glen, which
    serves. Resolving before normalising would 302 /Remedy-Match straight to
    /dr-glen and skip the 301 -- harmless -- but normalising the LEGACY name
    to the CANONICAL one and then re-normalising the case would bounce
    between the two forever."""
    _seed_page_slug(appmod.LOG_DB)
    first = client.get("/Remedy-Match", base_url=f"http://{PORTAL_HOST}")
    assert first.status_code == 301
    assert first.headers["Location"].endswith("/remedy-match")
    second = client.get("/remedy-match", base_url=f"http://{PORTAL_HOST}")
    assert second.status_code == 302
    assert second.headers["Location"].endswith("/dr-glen")
    final = client.get("/Remedy-Match", base_url=f"http://{PORTAL_HOST}",
                       follow_redirects=True)
    assert final.status_code == 200
    assert "<h1>Glen Swartwout</h1>" in final.get_data(as_text=True)


def test_a_view_is_recorded_under_the_affiliate_slug_not_the_page_slug(client):
    """Analytics key on the attribution slug forever, so a vanity rename does
    not split a practitioner's view history in half."""
    _seed_page_slug(appmod.LOG_DB)
    client.get("/dr-glen", base_url=f"http://{PORTAL_HOST}")
    assert _views(appmod.LOG_DB) == [("remedy-match", "storefront")]


def test_the_attribution_cookie_stays_the_affiliate_slug(client):
    """rm_ref is read at ~15 conversion sites and written onto stored lead rows
    as utm_source. Setting it to the page slug would orphan every commission
    earned from this page."""
    _seed_page_slug(appmod.LOG_DB)
    r = client.get("/dr-glen", base_url=f"http://{PORTAL_HOST}")
    assert "rm_ref=remedy-match" in r.headers.get("Set-Cookie", "")


def test_an_alias_still_301s_when_the_owner_has_a_page_slug(client):
    """The older alias feature and page_slug coexist: an alias points AT a
    canonical, a page_slug IS one. The alias hop stays a permanent 301 to a
    name that can never change (the affiliate slug); the changeable half of
    the journey is the 302 after it."""
    _seed_page_slug(appmod.LOG_DB)
    _claim(appmod.LOG_DB, "swartwout-clinic", canonical="remedy-match")
    r = client.get("/swartwout-clinic", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/remedy-match")
    final = client.get("/swartwout-clinic", base_url=f"http://{PORTAL_HOST}",
                       follow_redirects=True)
    assert final.status_code == 200
    body = final.get_data(as_text=True)
    assert "<h1>Glen Swartwout</h1>" in body
    # The chain must END at the page slug, not stop at the affiliate slug --
    # which is what it does today, where an alias resolves to a canonical that
    # knows nothing about page_slug.
    assert f'<link rel="canonical" href="https://{PORTAL_HOST}/dr-glen">' in body

