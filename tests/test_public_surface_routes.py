import os
import pytest
import sqlite3

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "1")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_api_sample_404s_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "")
    c = appmod.app.test_client()
    r = c.get("/api/sample")
    assert r.status_code == 404
    assert r.data == b""


def test_api_sample_returns_demo_payload(client):
    r = client.get("/api/sample")
    assert r.status_code == 200
    body = r.get_json()
    assert body["sample"] is True
    assert body["findings"]


def test_api_sample_needs_no_token_or_cookie(client):
    """Public means public — no auth of any kind."""
    r = client.get("/api/sample")
    assert r.status_code == 200


def test_api_sample_is_noindex(client):
    assert client.get("/api/sample").headers.get("X-Robots-Tag") == "noindex"


def test_sample_page_serves_html(client):
    r = client.get("/sample")
    assert r.status_code == 200
    assert b"<html" in r.data.lower()


def test_sample_page_is_noindex(client):
    r = client.get("/sample")
    assert r.headers.get("X-Robots-Tag") == "noindex"


def test_sample_page_404s_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "")
    c = appmod.app.test_client()
    r = c.get("/sample")
    assert r.status_code == 404
    assert r.data == b""


def test_sample_page_loads_no_third_party_scripts(client):
    """No trackers on public health-adjacent surfaces. Every large pixel
    settlement in this space was a private class action, not an OCR action."""
    lowered = client.get("/sample").data.decode("utf-8", "replace").lower()
    for needle in ("googletagmanager", "google-analytics", "connect.facebook",
                   "facebook.net", "hotjar", "fullstory", "segment.com",
                   "clarity.ms", "doubleclick"):
        assert needle not in lowered, f"third-party tracker present: {needle}"


def _check_no_remote_assets(html, page_name="page"):
    """Helper: verify HTML has no off-origin script/style/img/iframe.

    Covers: src=/href= with http://, https://, or protocol-relative "//";
    CSS url(...) references (inline <style> blocks or style= attributes);
    and @import (bare-string or url() form). Same-origin relative paths
    (e.g. href="/api/sample") are intentionally allowed."""
    import re as _re

    remote = []
    # src="..."/href="..." pointing off-origin (absolute http(s) or protocol-relative //)
    remote += _re.findall(
        r'(?:src|href)\s*=\s*["\'](?:https?:)?//[^"\']*', html, _re.I
    )
    # CSS url(...) references, quoted or bare, off-origin
    remote += _re.findall(
        r'url\(\s*["\']?(?:https?:)?//[^)"\']*', html, _re.I
    )
    # @import, either "url(...)" form or bare quoted-string form
    remote += _re.findall(
        r'@import\s+["\']?(?:url\(\s*)?["\']?(?:https?:)?//[^;)"\']*', html, _re.I
    )
    assert remote == [], f"{page_name}: off-origin assets: {remote}"


def _check_no_intake_elements(html, page_name="page"):
    """Helper: verify HTML has no scheduling widget, symptom checker, or login form."""
    lowered = html.lower()
    assert "<form" not in lowered, f"{page_name}: found <form"
    assert "<iframe" not in lowered, f"{page_name}: found <iframe"
    assert "type=\"password\"" not in lowered, f"{page_name}: found password input"
    assert "type=\"email\"" not in lowered, f"{page_name}: found email input"
    assert "type=\"tel\"" not in lowered, f"{page_name}: found tel input"
    for needle in ("calendly", "acuityscheduling", "schedule"):
        assert needle not in lowered, f"{page_name}: intake widget marker present: {needle}"


def test_sample_page_loads_no_remote_assets(client):
    """No off-origin script/style/img/iframe at all — the strong form of the
    no-tracker rule, and the one a future marketing change would violate first.

    Covers: src=/href= with http://, https://, or protocol-relative "//";
    CSS url(...) references (inline <style> blocks or style= attributes);
    and @import (bare-string or url() form). Same-origin relative paths
    (e.g. href="/api/sample") are intentionally allowed."""
    html = client.get("/sample").data.decode("utf-8", "replace")
    _check_no_remote_assets(html, page_name="/sample")


def test_sample_page_has_no_intake_elements(client):
    """No scheduling widget, symptom checker, or login form on a public page."""
    html = client.get("/sample").data.decode("utf-8", "replace")
    _check_no_intake_elements(html, page_name="/sample")


def _seed_affiliate(db_path, slug="prof-jane-doe", email="jane@example.com",
                    organization="Doe Wellness", notes=""):
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
        "INSERT INTO affiliate_signups (created_at,name,email,organization,slug,token,status,notes)"
        " VALUES ('2026-01-01','Jane Doe',?,?,?,'tok','approved',?)",
        (email, organization, slug, notes))
    cx.commit()
    cx.close()


@pytest.fixture
def client_with_affiliate(monkeypatch, tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed_affiliate(db)
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "1")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


@pytest.fixture
def client_with_leaky_affiliate(monkeypatch, tmp_path):
    """Seeded with values that trip the leak-detector needles by CONTENT, not
    just by field/key name -- see test_storefront_api_leaks_no_commercial_terms.
    The prior fixture's email was 'jane@example.com', which never contains the
    literal substring 'email', so a real email leak slipped past a needle list
    that only checked for the word 'email'. 'wholesale@example.com' trips the
    'wholesale' needle directly; 'margin 40% revenue' in notes trips 'margin'
    and 'revenue' directly."""
    db = str(tmp_path / "chat_log.db")
    _seed_affiliate(db, email="wholesale@example.com", organization="Doe Wellness",
                    notes="margin 40% revenue")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setenv("PUBLIC_SURFACE_ENABLED", "1")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_storefront_page_renders(client_with_affiliate):
    r = client_with_affiliate.get("/p/prof-jane-doe")
    assert r.status_code == 200
    assert r.headers.get("X-Robots-Tag") == "noindex"


def test_storefront_unknown_slug_404s(client_with_affiliate):
    assert client_with_affiliate.get("/p/no-such-person").status_code == 404


def test_storefront_sets_attribution_cookie(client_with_affiliate):
    r = client_with_affiliate.get("/p/prof-jane-doe")
    assert "rm_ref=prof-jane-doe" in r.headers.get("Set-Cookie", "")


def test_storefront_api_returns_whitelisted_payload(client_with_affiliate):
    body = client_with_affiliate.get("/api/p/prof-jane-doe").get_json()
    assert body["practitioner_name"] == "Jane Doe"
    assert "profit_disclosure" in body


def test_storefront_api_leaks_no_commercial_terms(client_with_leaky_affiliate):
    """The Thorne lesson: Thorne shipped wholesalePrice beside retailPrice in
    plain page source, letting patients compute their practitioner's margin.

    Only the /api/p/<slug> body is checked here. static/practitioner-storefront.html
    is a static file served byte-identical for every slug (see app.py's
    send_from_directory route) and populated client-side from this API. A real
    commercial-data leak can therefore only ever reach the page through the API
    payload — the page's own markup has no per-slug data path to leak through.
    Asserting these needles against the page HTML would only be testing the
    template author's word choice in CSS/JS/copy, not detecting a data leak, and
    that vacuous check previously induced a real regression: to dodge the literal
    string "margin", every CSS `margin:` rule in the template was rewritten to
    `padding:` plus flexbox centering, which broke vertical rhythm for no
    security benefit. So: check the data channel (API), not the static shell.

    Fixture values are chosen to trip the needles by CONTENT: a value-level
    leak test cannot rely on a needle matching a key/field NAME, because the
    leaked value's own text may never contain that name (jane@example.com
    never contains the substring "email"). See client_with_leaky_affiliate."""
    api = client_with_leaky_affiliate.get("/api/p/prof-jane-doe").data.decode("utf-8", "replace").lower()
    needles = (
        "wholesale", "margin", "markup", "msrp", "revenue", "commission",
        "earnings", "wallet", "patient", "order_volume", "email", "token",
    )
    for needle in needles:
        assert needle not in api, f"leaked {needle} in api"


def test_storefront_page_loads_no_remote_assets(client_with_affiliate):
    """No off-origin script/style/img/iframe at all — storefront must be as
    isolated as /sample, even though it's affiliate-personalized.

    Covers: src=/href= with http://, https://, or protocol-relative "//";
    CSS url(...) references (inline <style> blocks or style= attributes);
    and @import (bare-string or url() form). Same-origin relative paths
    (e.g. href="/api/p/...") are intentionally allowed."""
    html = client_with_affiliate.get("/p/prof-jane-doe").data.decode("utf-8", "replace")
    _check_no_remote_assets(html, page_name="/p/<slug>")


def test_storefront_page_has_no_intake_elements(client_with_affiliate):
    """No scheduling widget, symptom checker, or login form on storefront."""
    html = client_with_affiliate.get("/p/prof-jane-doe").data.decode("utf-8", "replace")
    _check_no_intake_elements(html, page_name="/p/<slug>")


def test_dispensary_route_still_works(client_with_affiliate):
    """Old links must never break. /dispensary/<code> is untouched by this work."""
    assert appmod.app.url_map.bind("localhost").match("/dispensary/abc123")[0] == "dispensary_landing"


def test_sample_slug_unknown_renders_bare_demo_not_404(client_with_affiliate):
    """A 404 would disclose which slugs exist. Render the bare demo instead."""
    r = client_with_affiliate.get("/sample/no-such-person")
    assert r.status_code == 200
    assert client_with_affiliate.get("/api/sample/no-such-person").get_json()["header"] is None


def test_sample_slug_without_approved_header_has_no_header(client_with_affiliate):
    body = client_with_affiliate.get("/api/sample/prof-jane-doe").get_json()
    assert body["sample"] is True
    assert body["header"] is None


def test_sample_slug_with_approved_header_includes_it(client_with_affiliate, tmp_path):
    from dashboard import share_header as sh
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    sh.init_share_headers_table(cx)
    sh.upsert_header(cx, "jane@example.com", "Jane", "Six months in.")
    sh.approve(cx, "jane@example.com")
    cx.close()
    body = client_with_affiliate.get("/api/sample/prof-jane-doe").get_json()
    assert body["header"] == {"display_name": "Jane", "body": "Six months in."}


def test_sample_slug_sets_attribution_cookie(client_with_affiliate):
    r = client_with_affiliate.get("/sample/prof-jane-doe")
    assert "rm_ref=prof-jane-doe" in r.headers.get("Set-Cookie", "")


def test_sample_slug_is_noindex(client_with_affiliate):
    assert client_with_affiliate.get("/sample/prof-jane-doe").headers.get("X-Robots-Tag") == "noindex"


def test_sample_unknown_slug_sets_no_attribution_cookie(client_with_affiliate):
    """A circulated garbage/unapproved link must never overwrite a real
    affiliate's 90-day rm_ref cookie. Still 200 with the bare demo -- a 404
    would disclose which slugs exist -- but no Set-Cookie at all."""
    r = client_with_affiliate.get("/sample/totally-bogus-slug")
    assert r.status_code == 200
    assert "rm_ref" not in r.headers.get("Set-Cookie", "")


def test_sample_unapproved_slug_sets_no_attribution_cookie(client_with_affiliate, tmp_path):
    """A slug that exists but is not (or no longer) approved must not set
    the cookie either -- the cookie must follow the same approval gate as
    view recording, not just pattern-validity."""
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.execute(
        "INSERT INTO affiliate_signups (created_at,name,email,organization,slug,token,status)"
        " VALUES ('2026-01-01','Pending Guy','pending@example.com','',?, 'tok2','pending')",
        ("pending-slug",))
    cx.commit()
    cx.close()
    r = client_with_affiliate.get("/sample/pending-slug")
    assert r.status_code == 200
    assert "rm_ref" not in r.headers.get("Set-Cookie", "")


def test_public_routes_never_call_get_portal_view(client_with_affiliate, monkeypatch):
    """Catches a future refactor quietly reconnecting the public surfaces to
    the private assembler."""
    from dashboard import portal_view as _pv

    def _boom(*a, **k):
        raise AssertionError("public route called get_portal_view")

    monkeypatch.setattr(_pv, "get_portal_view", _boom)
    for path in ("/sample", "/sample/prof-jane-doe", "/p/prof-jane-doe",
                 "/api/sample", "/api/sample/prof-jane-doe", "/api/p/prof-jane-doe"):
        assert client_with_affiliate.get(path).status_code == 200, path


# --- Fix wave: stored, published, whitelisted... and invisible --------------


# render_page_html reads view["slug"] only through _display_name's fallback
# (practitioner_name or slug or "Practitioner"). On the covered path that
# fallback is defensive and unexercised: app.py's affiliate_signups DDL
# declares `name TEXT NOT NULL`, and every signup writer rejects an empty
# name outright -- the gifting path falls back to the email address rather
# than ever writing an empty string -- so build_practitioner_storefront's
# `practitioner_name` is never empty for a real row. The fallback still
# earns its place in the code: it is belt-and-braces for a payload
# assembled some other way, outside build_practitioner_storefront's own
# NOT NULL guarantee -- exactly the shape the sentinels dict below
# constructs by hand.
#
# "slug" cannot join the substring sweep below regardless: any truthy
# canonical_url containing "sentinel-slug" would satisfy a bare substring
# check, whether or not the renderer's _display_name fallback still reads
# view["slug"] at all (confirmed by mutation below) -- slug reaches the
# page through canonical_url regardless of what _display_name does with
# it. So slug's presence is proven elsewhere: the value below is the test
# that proves it, not just an excuse to skip it. Keys carve fields out of
# the sweep; values name the test each carve-out owes evidence to, so a
# future carve-out cannot be added without also naming what proves it.
PROVEN_BY_THE_CALLER = {
    "slug": "tests/test_practitioner_site_render.py::"
            "test_canonical_binds_to_the_portal_host_not_the_funnel",
}


def test_every_public_field_is_actually_rendered_somewhere():
    """The failure four consecutive reviews caught by hand, handed to the suite.

    A field can be sanitized, drafted, reviewed, published to Postgres, listed
    in PRACTITIONER_PUBLIC_FIELDS and served in /api/p/<slug> -- and still be
    invisible to every human being, because nothing renders it. Every layer
    looks correct in isolation; only the whole chain shows the gap.

    Was xfail while the page was a JS shell that rendered five of fourteen
    fields. Section 5 server-rendered it, so this is now an ordinary guard:
    adding a key to the whitelist without rendering it is a red test, not a
    review finding.

    The oracle changed with the page. The old version searched the shell's
    source for field NAMES, because its JavaScript referenced v.field_name. A
    server-rendered page carries values, so each field gets a sentinel value
    and we assert the value arrives.

    Note: "services" and "location" each land in the page twice -- once in
    the visible body (the <ul> list / the ".loc" <p>) and once in the
    Person/ProfessionalService JSON-LD (serviceType / address). Both
    occurrences come from the same view key, so this is legitimate, not
    double-counting -- but it does mean a regression that deleted only the
    visible <ul> or <p class="loc"> and left the JSON-LD copy would still
    read as green here. That gap is accepted, not missed.

    "tagline" has the same shape, one layer over: build_description() reads
    the tagline field first (falling back to bio, then a neutral line) and
    writes whatever it returns into <meta name="description">, og:description,
    twitter:description AND the Person JSON-LD's "description" -- regardless
    of whether the visible <p class="tagline"> renderer still exists. A
    regression that deleted `_line("tagline", view.get("tagline"))` from the
    body would still leave "SentinelTagline" findable in four other places,
    so this sweep would keep reading green. Same accepted gap as above.
    """
    from dashboard import public_surface as ps
    from dashboard import practitioner_render as pr
    assert set(PROVEN_BY_THE_CALLER) <= set(ps.PRACTITIONER_PUBLIC_FIELDS), (
        "PROVEN_BY_THE_CALLER contains a key that isn't even in the "
        "whitelist -- a typo here would silently exclude nothing.")
    sentinels = {
        "slug": "sentinel-slug", "practitioner_name": "SentinelName",
        "practice_name": "SentinelPractice", "bio": "SentinelBio",
        "photo_url": "https://cdn.example/sentinel-photo.jpg",
        "logo_url": "https://cdn.example/sentinel-logo.jpg",
        "tagline": "SentinelTagline", "how_i_work": "SentinelHowIWork",
        "services": ["SentinelService"], "location": "SentinelLocation",
        "accepting_clients": True,
        "featured_products": [{"name": "SentinelProduct", "price": "$10"}],
        "catalog_url": "/sentinel-catalog",
        "profit_disclosure": "SentinelDisclosure",
        "practitioner_phone": "+15550199sentinel",
    }
    # Every whitelisted field must have a sentinel, "slug" included -- this
    # is what breaks the build the moment someone adds a field to
    # PRACTITIONER_PUBLIC_FIELDS and forgets this map. Do not narrow it to
    # only the fields swept below.
    assert set(sentinels) == set(ps.PRACTITIONER_PUBLIC_FIELDS), (
        "PRACTITIONER_PUBLIC_FIELDS changed. Add the new field to this map AND "
        "render it in dashboard/practitioner_render.py -- a field that reaches "
        "the payload and stops there is the exact defect this test exists for.")
    html = pr.render_page_html(dict(sentinels),
                               canonical_url="https://myhealingoasis.com/sentinel-slug")
    expected = {"slug": "sentinel-slug", "practitioner_name": "SentinelName",
                "practice_name": "SentinelPractice", "bio": "SentinelBio",
                "photo_url": "sentinel-photo.jpg", "logo_url": "sentinel-logo.jpg",
                "tagline": "SentinelTagline", "how_i_work": "SentinelHowIWork",
                "services": "SentinelService", "location": "SentinelLocation",
                "accepting_clients": "Accepting new clients",
                "featured_products": "SentinelProduct",
                "catalog_url": "/sentinel-catalog",
                "profit_disclosure": "SentinelDisclosure",
                "practitioner_phone": "+15550199sentinel"}
    # "slug" is proven by the caller (see PROVEN_BY_THE_CALLER above), not by
    # this substring sweep against render_page_html -- swept separately so
    # the sweep can only pass for fields it actually demonstrates.
    swept = {f: s for f, s in expected.items() if f not in PROVEN_BY_THE_CALLER}
    assert set(swept) | set(PROVEN_BY_THE_CALLER) == set(ps.PRACTITIONER_PUBLIC_FIELDS)
    missing = sorted(f for f, s in swept.items() if s not in html)
    assert not missing, (
        "public fields that reach the payload but never reach the page: "
        + ", ".join(missing))


def test_the_render_guard_is_measuring_the_right_thing():
    """Companion to the guard above: a guard that passes for a silly reason
    (empty render, renderer stubbed out) would look identical to a real pass.
    This asserts the renderer really produced a document."""
    from dashboard import practitioner_render as pr
    html = pr.render_page_html(
        {"slug": "x", "practitioner_name": "Mary Boyd"},
        canonical_url="https://myhealingoasis.com/x")
    assert len(html) > 200
    assert html.startswith("<!doctype html>")
    assert "<h1>Mary Boyd</h1>" in html
