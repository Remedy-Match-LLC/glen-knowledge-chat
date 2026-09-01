"""Route-level tests for the server-rendered practitioner page.

Every assertion here is on RAW RESPONSE BYTES, never a parsed DOM. The bots
this feature exists for -- iMessage, WhatsApp, Facebook, Slack -- do not
execute JavaScript, so a DOM-based test would pass on the old JS path and
hide the exact defect being fixed.
"""
import os
import sqlite3
import pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod

VIEW = {"slug": "mary-boyd", "practitioner_name": "Mary Boyd",
        "practice_name": "Fairbanks Wellness", "bio": "I work with nurses.",
        "photo_url": "", "logo_url": "", "services": [],
        "location": "Fairbanks, AK", "accepting_clients": True,
        "featured_products": [], "catalog_url": "/begin/explore",
        "profit_disclosure": "Your practitioner earns a portion.",
        "tagline": "Helping nurses stop running on empty", "how_i_work": ""}


@pytest.fixture
def portal(monkeypatch):
    """Serve as the portal host with the public surface on."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    from dashboard import practitioner_slugs as _ps
    # resolve_page, not the older alias resolve(): /<slug> and /p/<slug> both
    # go through it now, and it returns the AFFILIATE slug as a third value.
    # Stubbing it keeps these tests off a real database, exactly as the
    # resolve() stub it replaces did.
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)
    # Pin the exception-propagation mode. Roughly two dozen test files set
    # app.config["TESTING"] = True on this SHARED app object and never reset
    # it, so whether the test client converts an unhandled exception into a
    # 500 response or re-raises it depends on which files ran first. That is
    # invisible in a single-file run and fails in CI, where everything runs.
    # These tests assert the status a crawler sees, so force conversion.
    monkeypatch.setitem(appmod.app.config, "TESTING", False)
    monkeypatch.setattr(appmod.app, "testing", False, raising=False)
    return appmod.app.test_client()


def test_the_name_is_in_the_html_without_running_javascript(portal):
    body = portal.get("/mary-boyd").get_data(as_text=True)
    assert "<h1>Mary Boyd</h1>" in body
    assert "Helping nurses stop running on empty" in body
    assert "I work with nurses." in body


def test_share_preview_tags_are_in_the_initial_response(portal):
    body = portal.get("/mary-boyd").get_data(as_text=True)
    assert '<meta property="og:title" content="Mary Boyd — Fairbanks Wellness">' in body
    assert '<meta property="og:type" content="profile">' in body
    assert 'name="twitter:card"' in body


def test_canonical_binds_to_the_portal_host_not_the_funnel(portal):
    body = portal.get("/mary-boyd").get_data(as_text=True)
    assert '<link rel="canonical" href="https://myhealingoasis.com/mary-boyd">' in body
    assert "illtowell.com" not in body


def test_the_page_is_still_noindex(portal):
    r = portal.get("/mary-boyd")
    assert r.headers["X-Robots-Tag"] == "noindex"
    assert '<meta name="robots" content="noindex">' in r.get_data(as_text=True)


def test_the_referral_cookie_still_gets_set(portal):
    r = portal.get("/mary-boyd")
    assert "rm_ref=mary-boyd" in r.headers.get("Set-Cookie", "")


def test_an_unknown_slug_is_404_not_an_empty_page(portal, monkeypatch):
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront", lambda cx, slug: None)
    assert portal.get("/nobody-here").status_code == 404


def test_off_the_portal_host_it_is_still_404(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    assert appmod.app.test_client().get("/mary-boyd").status_code == 404


def test_a_payload_failure_does_not_claim_the_practitioner_is_gone(portal, monkeypatch):
    """Inverted 2026-08-29 on Glen's ruling. This test used to pin a 404 on a
    payload fault, on the theory that a catch-all must never turn one broken
    read into a site-wide 500.

    The ruling: "this practitioner does not exist" is not an acceptable thing
    to say when we could not look. To a client following a referral link, and
    to a crawler, a 404 is indistinguishable from the person having been
    removed -- a lie told about a real named practitioner during an outage
    they had nothing to do with.

    The blast-radius worry is smaller than it looked: check_shape rejects
    /.env, /wp-login.php, /xmlrpc.php and /.git/config before any query, so
    propagating costs a 500 on word-shaped probes like /admin during an outage
    where nothing else works either.

    The distinction the route now draws is "I looked and it is not there"
    (404, still tested below) versus "I could not look" (propagate).
    """
    from dashboard import public_surface as _psurf

    def boom(cx, slug):
        raise RuntimeError("postgres is down")
    monkeypatch.setattr(_psurf, "build_practitioner_storefront", boom)
    # Assert the status a crawler actually sees, not that the exception escapes
    # the test client -- 500 is what production returns, and 500 is the answer
    # that means "ask again later" rather than "she is gone".
    assert portal.get("/mary-boyd").status_code == 500


def test_a_resolve_failure_does_not_claim_the_practitioner_is_gone(portal, monkeypatch):
    """Same ruling, applied to the earlier of the two fault paths. The sqlite
    resolve is what a real outage takes down first, so this is the one that
    would actually have disowned a practitioner."""
    from dashboard import practitioner_slugs as _ps

    # db.Error is a TUPLE -- (sqlite3.Error, psycopg.Error) -- usable in an
    # `except` clause but NOT raisable. Raising it yields TypeError, which
    # sails straight past the route's `except db.Error` and produces a 500 for
    # the wrong reason: the test then passes whether the route propagates or
    # returns 404. Caught by mutation-testing this guard. Raise a member.
    def boom(cx, s):
        raise sqlite3.Error("sqlite is down")
    monkeypatch.setattr(_ps, "resolve_page", boom)
    assert portal.get("/mary-boyd").status_code == 500


def test_a_genuinely_unknown_slug_is_still_404(portal, monkeypatch):
    """The other half of the distinction: when the lookup SUCCEEDS and the
    slug is not a practitioner, 404 is the honest answer and must survive."""
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page", lambda cx, s: ("", "", ""))
    # Both resolvers, because a page_slug miss falls back to the older alias
    # table. Leaving the second one real would send this test at whatever
    # database LOG_DB happens to point at.
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("", ""))
    assert portal.get("/nobody-here").status_code == 404


def test_the_legacy_funnel_path_is_also_server_rendered(monkeypatch):
    """/p/<slug> on the funnel host is a live public URL — every one ever
    texted or printed still resolves there. Leaving it on the JS shell would
    keep the blank-preview bug alive on exactly those links."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    body = appmod.app.test_client().get("/p/mary-boyd").get_data(as_text=True)
    assert "<h1>Mary Boyd</h1>" in body
    assert '<meta property="og:title" content="Mary Boyd — Fairbanks Wellness">' in body


def test_the_legacy_path_declares_the_portal_canonical(monkeypatch):
    """Both pages point at ONE url. That is what collapses the duplicate."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    body = appmod.app.test_client().get("/p/mary-boyd").get_data(as_text=True)
    assert '<link rel="canonical" href="https://myhealingoasis.com/mary-boyd">' in body


def test_an_unset_portal_base_url_omits_canonical_instead_of_lying(monkeypatch):
    """A relative canonical_url resolves in the BROWSER against the current
    page's host. Served from illtowell.com/p/<slug>, a bare "/mary-boyd"
    would resolve to https://illtowell.com/mary-boyd -- exactly the
    funnel-host canonical the spec forbids -- arriving through a missing
    config value instead of a code bug. Every existing fixture in this file
    SETS PORTAL_BASE_URL, which is why nothing caught that. This test
    genuinely deletes it and asserts on the tag's absence, not on a
    substring that happens not to appear."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.delenv("PORTAL_BASE_URL", raising=False)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    r = appmod.app.test_client().get("/p/mary-boyd")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # the page must still render fully
    assert "<h1>Mary Boyd</h1>" in body
    assert '<meta property="og:title" content="Mary Boyd — Fairbanks Wellness">' in body
    assert '"@type": "Person"' in body
    # but must declare no canonical at all -- not an empty one, not a
    # relative one, and definitely not the funnel host
    assert 'rel="canonical"' not in body
    assert 'property="og:url"' not in body
    assert "illtowell.com" not in body
    assert 'href="/mary-boyd"' not in body


def test_the_two_routes_render_identical_bytes_for_the_same_view(monkeypatch):
    """The branch's central claim, from _render_practitioner_page's own
    docstring, is 'one helper, two callers, because a difference is a bug in
    every case.' Nothing pinned that until now. Same view, same canonical
    target (both resolve to https://myhealingoasis.com/mary-boyd): the
    response BODIES must be byte-identical between /<slug> on the portal
    host and /p/<slug> on the funnel host (where it renders instead of
    302ing, since the redirect only fires on the portal host)."""
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    portal_resp = appmod.app.test_client().get("/mary-boyd")

    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    funnel_resp = appmod.app.test_client().get("/p/mary-boyd")

    assert portal_resp.status_code == 200
    assert funnel_resp.status_code == 200
    assert portal_resp.data == funnel_resp.data


def _seed_affiliate(db_path, slug="mary-boyd", name="Mary Boyd",
                    organization="Fairbanks Wellness"):
    cx = sqlite3.connect(db_path)
    cx.executescript("""
      CREATE TABLE IF NOT EXISTS affiliate_signups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, name TEXT,
        email TEXT, organization TEXT DEFAULT '', website TEXT DEFAULT '',
        promo_method TEXT DEFAULT '', slug TEXT, token TEXT,
        status TEXT DEFAULT 'approved', notes TEXT DEFAULT '',
        referred_by TEXT DEFAULT '', short_url TEXT DEFAULT '');
    """)
    cx.execute(
        "INSERT INTO affiliate_signups (created_at,name,email,organization,slug,token,status)"
        " VALUES ('2026-01-01',?,'mary@example.com',?,?,'tok','approved')",
        (name, organization, slug))
    cx.commit()
    cx.close()


def test_a_route_exercises_the_real_payload_builder(monkeypatch, tmp_path):
    """Every other fixture in this file stubs build_practitioner_storefront
    and hand-writes accepting_clients: True -- which is exactly why Critical
    1 (accepting_clients defaulting to True for every unauthored profile)
    reached the live page unnoticed by any route-level test. This seeds a
    real affiliate_signups row and lets build_practitioner_storefront run
    for real, so a regression in ITS defaults (not just the renderer's
    handling of them) would show up here."""
    db_path = str(tmp_path / "chat_log.db")
    _seed_affiliate(db_path)
    monkeypatch.setattr(appmod, "LOG_DB", db_path)
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    from dashboard import practitioner_slugs as _ps
    monkeypatch.setattr(_ps, "resolve_page",
                        lambda cx, s: ("canonical", s, s))

    r = appmod.app.test_client().get("/mary-boyd")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<h1>Mary Boyd</h1>" in body
    assert "Fairbanks Wellness" in body
    # This practitioner has never authored a profile -- the real builder's
    # default for accepting_clients must be None, and the page must
    # therefore make no availability claim at all. This is Critical 1's
    # regression guard at the route level, not just the unit level.
    assert '<p class="accepting">' not in body
    assert "Accepting new clients" not in body
    assert "Not currently accepting new clients" not in body


def test_the_js_shell_is_gone():
    """The blank-preview-card bug lived in this file, and both public routes
    used to serve it. If a future change re-introduces it, the regression
    comes back silently — a browser would still look right.

    What this proves: the specific file at this path is deleted from disk.
    What it does NOT prove: that no route serves a JS-based storefront under
    a different filename, that no other file still references
    practitioner-storefront.html, or that the routes that used to serve it
    are still server-rendered today -- that guarantee comes from the tests
    above in this file (test_the_name_is_in_the_html_without_running_
    javascript and friends), not from this one."""
    import pathlib
    assert not (pathlib.Path(appmod.STATIC) / "practitioner-storefront.html").exists()
