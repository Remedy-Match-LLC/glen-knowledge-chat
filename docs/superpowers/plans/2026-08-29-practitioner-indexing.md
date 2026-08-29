# Practitioner Indexing Implementation Plan (Section 5b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a practitioner page be shared the moment Glen approves it, while it stays out of Google until it carries enough content to be worth indexing — then expose the qualifying pages to search through a host-aware `robots.txt` and a practitioner sitemap.

**Architecture:** A single predicate, `is_indexable(view)`, decides whether one page may be indexed. It is consulted in exactly two places: the `noindex` decision on the page itself, and the sitemap's row filter. One predicate, two call sites, so a page can never be listed in the sitemap while telling crawlers not to index it.

**Tech Stack:** Python 3, Flask. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`, Section 5.

## Prerequisite — do not start this plan early

**Plan 5a (`2026-08-29-practitioner-server-render.md`) must be merged, deployed, and seen by Glen on a real page first.** The spec's Rollout section: *"indexing last - indexing is the only step that is hard to walk back, since a page Google has crawled stays in the index after the page changes."*

A page indexed while thin stays indexed while thin. There is no undo, only a slow re-crawl. Everything in 5a is reversible; nothing here fully is.

**Additionally, this plan should not ship until at least one practitioner has actually authored a profile.** As of 2026-08-29, zero of the 23 portal practitioners are self-authored, so `is_indexable` would return `False` for every row and the sitemap would be empty. That is correct behaviour, but shipping an empty sitemap proves nothing. Mary Boyd was invited on 2026-08-29 and is expected to be the first.

## Global Constraints

Copied verbatim from the spec where the spec states a value:

- **Separate `live` from `indexed` as two independent flags.** A page is publishable and shareable the moment Glen approves it, while staying `noindex` until it clears a minimum content bar: **tagline, bio, and photo present.**
- **A page carrying only a name is thin content**, bad for the practitioner and a drag on the hosting domain.
- **`noindex` remains the default**, lifted only for approved, published, content-complete profiles. **Drafts, pending review, scraped rows never self-authored, and the sample portal all keep it.**
- **Host-aware `robots.txt`**, using the `_on_portal_host()` pattern: myhealingoasis.com allows practitioner paths, **illtowell.com unchanged.**
- **A practitioner sitemap on the portal host**, listing only indexable profiles. **It must bind to `PORTAL_BASE_URL`, not `PUBLIC_BASE_URL`** — the two existing sitemaps hardcode the funnel host, and copying that would emit wrong-host URLs for every practitioner.
- **Assert on raw HTTP response bytes, not a rendered DOM.**
- **Mutation-test every guard** — including the `noindex` guard by name.

## Context the implementer needs

**There is no `robots.txt` today.** Both `https://illtowell.com/robots.txt` and `https://myhealingoasis.com/robots.txt` return **404**, verified 2026-08-29. This matters more than it looks: a 404 means *everything is crawlable*, so "illtowell.com unchanged" means the funnel must **keep returning 404**, not gain a permissive robots file. Adding one for the funnel would be a site-wide SEO change nobody asked for. Task 3 covers this explicitly.

**The two existing sitemaps are the pattern and also the trap.** `app.py` has `/learn/sitemap.xml` and `/mentors/sitemap.xml`, both calling a `render_sitemap_xml(rows, base_url)` duplicated in `dashboard/topic_render.py:219` and `dashboard/mentor_render.py:223`. Both hardcode `PUBLIC_BASE_URL`. Read one for the XML shape; do **not** copy the base-URL argument.

**`_on_portal_host()`** (in `app.py`, find with `grep -n 'def _on_portal_host' app.py`) returns `True` only when the request arrived on the host named by `PORTAL_BASE_URL` and that host differs from `PUBLIC_BASE_URL`. In prod, `PORTAL_BASE_URL=https://myhealingoasis.com` and `PUBLIC_BASE_URL=https://illtowell.com`, so it works. In dev both may be unset, and it returns `False` — your tests must monkeypatch it rather than depend on the environment.

**The payload** comes from `dashboard.public_surface.build_practitioner_storefront(cx, slug)`. Self-authored fields are non-empty only when `profile_self_authored_at` is set in Postgres; a scraped row yields name and practice name only. So "never self-authored" and "no tagline/bio/photo" collapse into the same observable state, which is why `is_indexable` can be a pure function of the payload.

**Line numbers drift.** `app.py` is ~52,000 lines and several sessions commit to it. Anchor on content with `grep -n`, never on a line number quoted here.

---

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/practitioner_render.py` (modify) | Gains `is_indexable(view)` and takes an `indexable` argument in `render_page_html`. Stays pure. |
| `dashboard/practitioner_sitemap.py` (new) | `render_sitemap_xml(rows, base_url)` for practitioner pages. A separate module rather than a third copy inside an existing renderer, because it binds to a different host and that difference should be visible in the import. |
| `app.py` (modify) | `robots.txt` route, `/sitemap-practitioners.xml` route, and passing `indexable` into the renderer. |
| `tests/test_practitioner_indexing.py` (new) | The `is_indexable` predicate and the `noindex` guard. |
| `tests/test_practitioner_sitemap.py` (new) | Sitemap XML, host binding, and the filter agreeing with the predicate. |
| `tests/test_robots_txt.py` (new) | Host-aware robots, including the funnel staying untouched. |

---

## Task 1: The indexability predicate

**Files:**
- Modify: `dashboard/practitioner_render.py`
- Test: `tests/test_practitioner_indexing.py` (new)

**Interfaces:**
- Consumes: the `view` payload shape from plan 5a.
- Produces: `is_indexable(view) -> bool`. Tasks 2 and 4 both call it. Do not duplicate the rule in either.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_indexing.py
"""The content bar. A page carrying only a name is thin content -- bad for the
practitioner and a drag on the hosting domain -- so it is shareable but not
indexable."""
import pytest
from dashboard import practitioner_render as pr


def _view(**over):
    v = {"slug": "mary-boyd", "practitioner_name": "Mary Boyd",
         "practice_name": "", "bio": "", "photo_url": "", "logo_url": "",
         "services": [], "location": "", "accepting_clients": True,
         "featured_products": [], "catalog_url": "/begin/explore",
         "profit_disclosure": "d", "tagline": "", "how_i_work": ""}
    v.update(over)
    return v


def _complete(**over):
    return _view(tagline="Helping nurses stop running on empty",
                 bio="I work with nurses and shift workers on sleep and energy.",
                 photo_url="https://cdn.example/mary.jpg", **over)


def test_a_complete_profile_is_indexable():
    assert pr.is_indexable(_complete()) is True


def test_a_name_only_profile_is_not_indexable():
    """Today this is EVERY practitioner: zero of 23 are self-authored."""
    assert pr.is_indexable(_view()) is False


@pytest.mark.parametrize("missing", ["tagline", "bio", "photo_url"])
def test_each_required_field_is_load_bearing(missing):
    """Spec names three: tagline, bio, and photo. Drop any one and the bar
    is not met -- this is the test that catches a silently relaxed rule."""
    assert pr.is_indexable(_complete(**{missing: ""})) is False


def test_whitespace_is_not_content():
    assert pr.is_indexable(_complete(tagline="   \n  ")) is False


def test_a_missing_key_is_treated_as_absent_not_an_error():
    v = _complete()
    del v["photo_url"]
    assert pr.is_indexable(v) is False


def test_none_view_is_not_indexable():
    assert pr.is_indexable(None) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_indexing.py -q -p no:randomly`

Expected: FAIL with `AttributeError: module 'dashboard.practitioner_render' has no attribute 'is_indexable'`.

- [ ] **Step 3: Write the implementation**

Add to `dashboard/practitioner_render.py`:

```python
# The spec's content bar, named so it can be grepped and so a future reader
# sees these are the three the spec chose, not three that accumulated.
INDEXABLE_REQUIRED_FIELDS = ("tagline", "bio", "photo_url")


def is_indexable(view):
    """True when this page clears the minimum content bar for search.

    Live and indexed are deliberately separate: a page is shareable the moment
    Glen approves it, and indexable only once it carries a tagline, a bio and
    a photo. A page with only a name is thin content -- bad for the
    practitioner whose name is on it, and a drag on the domain hosting it.

    Fails closed. A missing key, a None view, or a whitespace-only value all
    mean "not indexable", because the cost of wrongly indexing a thin page is
    an entry Google keeps long after the page improves.

    This is the ONLY definition of the bar. Both the noindex decision and the
    sitemap filter call it, so a page can never be listed in the sitemap while
    telling crawlers to ignore it.
    """
    if not view:
        return False
    return all((view.get(f) or "").strip() for f in INDEXABLE_REQUIRED_FIELDS)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_indexing.py -q -p no:randomly`

Expected: PASS, 9 tests.

- [ ] **Step 5: Mutation-test the bar**

Drop `"photo_url"` from `INDEXABLE_REQUIRED_FIELDS`. Confirm the `test_each_required_field_is_load_bearing[photo_url]` case goes **red**. Restore, confirm green. Repeat for `tagline`. This is the guard the spec names; watch it fail twice.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py tests/test_practitioner_indexing.py
git commit -m "feat(practitioner): content bar for indexability"
```

---

## Task 2: Lift noindex for qualifying pages only

**Files:**
- Modify: `dashboard/practitioner_render.py` (`render_page_html`), `app.py` (`_render_practitioner_page`, the helper both public routes call)
- Test: `tests/test_practitioner_indexing.py`

**Interfaces:**
- Consumes: `is_indexable(view)` from Task 1.
- Produces: `render_page_html(view, *, canonical_url, indexable=False)`. **The default is `False`.** A caller that forgets the argument gets `noindex`, which is the safe direction. Task 4 relies on the same predicate but not on this signature.

- [ ] **Step 1: Write the failing test**

```python
CANON = "https://myhealingoasis.com/mary-boyd"


def test_noindex_is_the_default_when_the_caller_says_nothing():
    """A forgotten argument must fail toward noindex, never toward indexing."""
    html = pr.render_page_html(_complete(), canonical_url=CANON)
    assert '<meta name="robots" content="noindex">' in html


def test_a_qualifying_page_may_be_indexed():
    html = pr.render_page_html(_complete(), canonical_url=CANON, indexable=True)
    assert 'content="noindex"' not in html
    assert '<meta name="robots" content="index,follow">' in html


def test_a_thin_page_stays_noindex_even_if_the_caller_insists():
    """Defence in depth: the renderer re-checks the bar rather than trusting
    its caller. The route and the sitemap must not be able to disagree."""
    html = pr.render_page_html(_view(), canonical_url=CANON, indexable=True)
    assert '<meta name="robots" content="noindex">' in html
```

And in `tests/test_practitioner_site_render.py` (from plan 5a), add:

```python
COMPLETE = dict(VIEW, tagline="Helping nurses stop running on empty",
                bio="I work with nurses.", photo_url="https://cdn.example/m.jpg")


def test_a_thin_page_still_carries_the_noindex_header(portal):
    r = portal.get("/mary-boyd")
    assert r.headers["X-Robots-Tag"] == "noindex"


def test_a_complete_page_drops_the_noindex_header(portal, monkeypatch):
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(COMPLETE, slug=slug))
    r = portal.get("/mary-boyd")
    assert "X-Robots-Tag" not in r.headers
    assert '<meta name="robots" content="index,follow">' in r.get_data(as_text=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_indexing.py tests/test_practitioner_site_render.py -q -p no:randomly`

Expected: FAIL — `render_page_html() got an unexpected keyword argument 'indexable'`.

- [ ] **Step 3: Write the implementation**

In `dashboard/practitioner_render.py`, change the signature and the robots line:

```python
def render_page_html(view, *, canonical_url, indexable=False):
    """...(keep the existing docstring, and add:)

    `indexable` defaults to False so a caller that forgets it gets noindex.
    The value is AND-ed with is_indexable(view) rather than trusted, so the
    route and the sitemap cannot disagree about one page.
    """
```

Replace the hardcoded robots meta with:

```python
    robots = ('<meta name="robots" content="index,follow">'
              if (indexable and is_indexable(view))
              else '<meta name="robots" content="noindex">')
```

and interpolate `{robots}` where the literal noindex tag was.

In `app.py`, change `_render_practitioner_page` — the helper plan 5a introduced, which **both** `/​<slug>` and `/p/<slug>` call. Changing it once is what keeps the canonical page and its legacy alias from disagreeing about whether a page may be indexed. Its current body ends:

```python
    resp = Response(
        _prender.render_page_html(
            view, canonical_url=f"{portal_base}/{canonical_slug}"),
        mimetype="text/html")
    resp.headers["X-Robots-Tag"] = "noindex"
```

Replace those lines with:

```python
    indexable = _prender.is_indexable(view)
    resp = Response(
        _prender.render_page_html(
            view, canonical_url=f"{portal_base}/{canonical_slug}",
            indexable=indexable),
        mimetype="text/html")
    # The header and the meta tag must agree, so both derive from one call.
    # Only set the header when the page is NOT indexable: an "index"
    # X-Robots-Tag asserts nothing a crawler needs, and its absence is the
    # correct signal.
    if not indexable:
        resp.headers["X-Robots-Tag"] = "noindex"
```

Leave the `Cache-Control` header and the `rm_ref` cookie exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_indexing.py tests/test_practitioner_site_render.py -q -p no:randomly`

Expected: PASS.

- [ ] **Step 5: Mutation-test the noindex guard**

The spec names this guard specifically. Change the renderer's condition to `if indexable:` (dropping the `is_indexable(view)` re-check). Confirm `test_a_thin_page_stays_noindex_even_if_the_caller_insists` goes **red**. Restore, confirm green. Then remove the `if not indexable:` condition in `app.py` so the header is never set, and confirm `test_a_thin_page_still_carries_the_noindex_header` goes **red**. Restore.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py app.py tests/test_practitioner_indexing.py tests/test_practitioner_site_render.py
git commit -m "feat(practitioner): lift noindex only for content-complete pages"
```

---

## Task 3: Host-aware robots.txt

**Files:**
- Modify: `app.py`
- Test: `tests/test_robots_txt.py` (new)

**Interfaces:**
- Consumes: `_on_portal_host()`, already in `app.py`.
- Produces: a `GET /robots.txt` route. Nothing later consumes it.

**The funnel must keep returning 404.** There is no `robots.txt` on either host today, so a 404 is the current — and correct — behaviour for illtowell.com. The spec says "illtowell.com unchanged", and adding a permissive robots file there would be a site-wide SEO change outside this plan's scope.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_robots_txt.py
"""Host-aware robots.txt.

There is no robots.txt on either host today -- both 404, verified 2026-08-29.
So "illtowell.com unchanged" means it must KEEP 404ing. Introducing a robots
file on the funnel would be a site-wide SEO change nobody asked for.
"""
import os
import pytest
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod


def test_the_funnel_host_still_has_no_robots_file(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    assert appmod.app.test_client().get("/robots.txt").status_code == 404


def test_the_portal_host_serves_robots(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    r = appmod.app.test_client().get("/robots.txt")
    assert r.status_code == 200
    assert r.mimetype == "text/plain"
    body = r.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Allow: /" in body


def test_portal_robots_points_at_its_own_sitemap_on_its_own_host(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    body = appmod.app.test_client().get("/robots.txt").get_data(as_text=True)
    assert "Sitemap: https://myhealingoasis.com/sitemap-practitioners.xml" in body
    assert "illtowell.com" not in body


def test_portal_robots_keeps_crawlers_out_of_the_signed_in_surfaces(monkeypatch):
    """Practitioner workspace pages are behind a token but are still URLs a
    crawler can find in a shared link."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
    body = appmod.app.test_client().get("/robots.txt").get_data(as_text=True)
    for path in ("/practitioner/", "/api/", "/console/"):
        assert f"Disallow: {path}" in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_robots_txt.py -q -p no:randomly`

Expected: FAIL — the portal case 404s, since no route exists.

- [ ] **Step 3: Write the implementation**

Add to `app.py`, near the other host-aware routes:

```python
@app.route("/robots.txt")
def robots_txt():
    """Host-aware robots.

    The funnel gets a 404, which is what it has always returned -- there is no
    robots.txt on illtowell.com and introducing one here would be a site-wide
    SEO change outside this feature. The portal host gets a real file that
    allows practitioner pages and keeps crawlers out of the signed-in
    surfaces, whose URLs can leak through a shared link even though the
    content behind them needs a token.

    Individual thin pages are excluded by their own noindex meta and header,
    not here: robots.txt cannot express "index this practitioner but not that
    one", and a crawler that is disallowed can still index a URL it saw
    elsewhere. The per-page tag is the real gate; this file is the map.
    """
    if not _on_portal_host():
        return ("", 404)
    base = (os.environ.get("PORTAL_BASE_URL") or "").rstrip("/")
    lines = ["User-agent: *", "Allow: /",
             "Disallow: /practitioner/", "Disallow: /api/", "Disallow: /console/"]
    if base:
        lines.append(f"Sitemap: {base}/sitemap-practitioners.xml")
    return Response("\n".join(lines) + "\n", mimetype="text/plain")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_robots_txt.py -q -p no:randomly`

Expected: PASS, 4 tests.

- [ ] **Step 5: Mutation-test the host guard**

Remove the `if not _on_portal_host(): return ("", 404)` lines. Confirm `test_the_funnel_host_still_has_no_robots_file` goes **red**. Restore, confirm green.

- [ ] **Step 6: Check the slug-collision guard**

`/<slug>` is a catch-all on the portal host, and `robots.txt` is now a named static route that beats it. Run the existing guard:

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_slug_route_collision.py -q -p no:randomly`

Expected: PASS. If it fails because `robots.txt` is not a legal slug shape anyway, report what it says rather than editing the guard.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_robots_txt.py
git commit -m "feat(practitioner): host-aware robots.txt on the portal host"
```

---

## Task 4: The practitioner sitemap

**Files:**
- Create: `dashboard/practitioner_sitemap.py`
- Modify: `app.py`
- Test: `tests/test_practitioner_sitemap.py` (new)

**Interfaces:**
- Consumes: `is_indexable(view)` from Task 1.
- Produces: `render_sitemap_xml(rows, base_url) -> str`, where `rows` is a list of dicts each carrying at least `slug`, and optionally `updated_at`. Nothing later consumes it.

**A separate module, not a third copy inside an existing renderer.** `render_sitemap_xml` already exists twice, in `dashboard/topic_render.py` and `dashboard/mentor_render.py`, and both hardcode the funnel host. A third copy in one of those files would inherit that assumption by proximity. The whole point of this one is that it binds elsewhere, and the import should say so.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_sitemap.py
import pytest
from dashboard import practitioner_sitemap as ps

BASE = "https://myhealingoasis.com"


def test_a_row_becomes_a_loc_on_the_portal_host():
    xml = ps.render_sitemap_xml([{"slug": "mary-boyd"}], BASE)
    assert "<loc>https://myhealingoasis.com/mary-boyd</loc>" in xml
    assert "illtowell.com" not in xml


def test_the_url_is_the_clean_slug_not_the_legacy_path():
    """/p/<slug> is legacy and 301s to /<slug>. A sitemap full of redirects
    wastes crawl budget and muddies the canonical."""
    xml = ps.render_sitemap_xml([{"slug": "mary-boyd"}], BASE)
    assert "/p/mary-boyd" not in xml


def test_lastmod_is_emitted_when_a_timestamp_is_present():
    xml = ps.render_sitemap_xml(
        [{"slug": "mary-boyd", "updated_at": "2026-08-29T12:00:00Z"}], BASE)
    assert "<lastmod>2026-08-29</lastmod>" in xml


def test_lastmod_is_omitted_rather_than_emitted_empty():
    xml = ps.render_sitemap_xml([{"slug": "mary-boyd"}], BASE)
    assert "<lastmod>" not in xml


def test_an_empty_roster_is_still_valid_xml():
    """This is the state on the day it ships: zero self-authored profiles."""
    xml = ps.render_sitemap_xml([], BASE)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<urlset" in xml and "</urlset>" in xml
    assert "<url>" not in xml


def test_a_slug_with_markup_cannot_break_the_xml():
    xml = ps.render_sitemap_xml([{"slug": 'a"><script>'}], BASE)
    assert "<script>" not in xml


def test_a_trailing_slash_on_the_base_does_not_double_up():
    xml = ps.render_sitemap_xml([{"slug": "mary-boyd"}], BASE + "/")
    assert "myhealingoasis.com//mary-boyd" not in xml
```

Route-level, in the same file:

```python
import os
if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)
import app as appmod


@pytest.fixture
def portal(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: True)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", BASE)
    return appmod.app.test_client()


def test_the_sitemap_is_404_on_the_funnel_host(monkeypatch):
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    assert appmod.app.test_client().get("/sitemap-practitioners.xml").status_code == 404


def test_only_indexable_profiles_are_listed(portal, monkeypatch):
    """The sitemap filter and the noindex decision must agree. A page listed
    here but carrying noindex is a contradiction crawlers report as an error."""
    thin = {"slug": "thin-one", "practitioner_name": "Thin", "tagline": "",
            "bio": "", "photo_url": ""}
    full = {"slug": "mary-boyd", "practitioner_name": "Mary", "tagline": "t",
            "bio": "b", "photo_url": "https://cdn.example/m.jpg"}
    monkeypatch.setattr(appmod, "_sitemap_practitioner_views",
                        lambda: [thin, full])
    body = portal.get("/sitemap-practitioners.xml").get_data(as_text=True)
    assert "mary-boyd" in body
    assert "thin-one" not in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_sitemap.py -q -p no:randomly`

Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.practitioner_sitemap'`.

- [ ] **Step 3: Write the module**

```python
# dashboard/practitioner_sitemap.py
"""Sitemap for practitioner pages on the portal host.

Its own module, rather than a third copy of render_sitemap_xml beside the two
in topic_render.py and mentor_render.py, because those both hardcode
PUBLIC_BASE_URL (the funnel) and this one must bind to PORTAL_BASE_URL. A
copy living next to them would inherit that assumption by proximity; the
separate import is the reminder.
"""
import html


def render_sitemap_xml(rows, base_url):
    """Build the practitioner sitemap.

    `rows` are payload dicts carrying at least `slug`, already filtered to
    indexable pages by the caller. `base_url` is the PORTAL base, not the
    funnel -- a funnel-host URL here would point every entry at a page that
    does not exist there.

    Emits the clean /<slug> URL, never the legacy /p/<slug>, which 301s. A
    sitemap full of redirects wastes crawl budget and muddies the canonical.
    """
    base = (base_url or "").rstrip("/")
    parts = []
    for r in rows:
        slug = str(r.get("slug") or "").strip()
        if not slug:
            continue
        loc = html.escape(f"{base}/{slug}", quote=True)
        stamp = str(r.get("updated_at") or "")[:10]
        lastmod = f"<lastmod>{html.escape(stamp, quote=True)}</lastmod>" if stamp else ""
        parts.append(f"<url><loc>{loc}</loc>{lastmod}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(parts) + "</urlset>")
```

- [ ] **Step 4: Write the route**

Add to `app.py`:

```python
def _sitemap_practitioner_views():
    """Every approved practitioner's public payload, for the sitemap.

    Split out from the route so a test can supply rows without standing up a
    database. Fails closed to an empty list: an empty sitemap is a correct
    answer on a day when nobody has authored a profile yet, and a 500 on a
    crawler-facing URL is not.
    """
    from dashboard import public_surface as _psurf
    out = []
    try:
        with db.connect(LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            slugs = [r["slug"] for r in cx.execute(
                "SELECT slug FROM affiliate_signups"
                " WHERE status='approved' AND slug IS NOT NULL AND slug<>''"
            ).fetchall()]
        with db.connect(LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            for s in slugs:
                v = _psurf.build_practitioner_storefront(cx, s)
                if v:
                    out.append(v)
    except Exception as e:  # noqa: BLE001
        print(f"[sitemap-practitioners] roster read failed: {e!r}", flush=True)
        return []
    return out


@app.route("/sitemap-practitioners.xml")
def sitemap_practitioners():
    """Practitioner sitemap, portal host only.

    Lists ONLY pages that clear the content bar, using the same is_indexable
    predicate the page itself uses for its noindex decision. Listing a page
    here while it carries noindex is a contradiction crawlers report as an
    error, so the two must never diverge -- which is why this is one function
    called twice, not two rules.
    """
    if not _on_portal_host():
        return ("", 404)
    if not _public_surface_enabled():
        return ("", 404)
    from dashboard import practitioner_render as _prender
    from dashboard import practitioner_sitemap as _psm
    rows = [v for v in _sitemap_practitioner_views() if _prender.is_indexable(v)]
    base = (os.environ.get("PORTAL_BASE_URL") or "").rstrip("/")
    return Response(_psm.render_sitemap_xml(rows, base), mimetype="application/xml")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_sitemap.py -q -p no:randomly`

Expected: PASS, 9 tests.

- [ ] **Step 6: Mutation-test the sitemap filter**

Remove the `if _prender.is_indexable(v)` filter so every approved row is listed. Confirm `test_only_indexable_profiles_are_listed` goes **red**. Restore, confirm green. Then change the route's `base` to read `PUBLIC_BASE_URL` and confirm `test_a_row_becomes_a_loc_on_the_portal_host`'s route-level sibling catches it — if no test covers that, add one before moving on.

- [ ] **Step 7: Run the wider suite**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner*.py tests/test_robots_txt.py tests/test_slug_route_collision.py -q -p no:randomly`

Two failures are expected and are **not yours** — both reproduce on unmodified `origin/main`, verified 2026-08-29:
- `tests/test_practitioner_personal_order.py::test_personal_card_return_books_one_sales_receipt`
- `tests/test_membership.py::test_studio_credit_post_inserts_intent_sends_glen_notification`

- [ ] **Step 8: Commit**

```bash
git add dashboard/practitioner_sitemap.py app.py tests/test_practitioner_sitemap.py
git commit -m "feat(practitioner): sitemap of indexable profiles on the portal host"
```

---

## Verification before merge

```bash
curl -s https://myhealingoasis.com/robots.txt
curl -s https://myhealingoasis.com/sitemap-practitioners.xml
curl -s -o /dev/null -w '%{http_code}\n' https://illtowell.com/robots.txt   # must be 404
curl -sI https://myhealingoasis.com/mary-boyd | grep -i x-robots-tag
```

**Read the sitemap before believing it.** An empty `<urlset>` is the correct answer while no practitioner has authored a complete profile — but an empty sitemap and a broken roster query look identical from outside. Confirm against the database which practitioners *should* qualify, and check the count matches.

Then, and only then, submit the sitemap in Google Search Console. That step is Glen's, not the implementer's, and it is the one action in this plan with no undo.

---

## Self-review notes

**Spec coverage for the indexing half of Section 5.** `live` vs `indexed` as independent flags: Task 1's predicate is the `indexed` axis; `live` is approval, which already exists from section 2a. Content bar of tagline, bio, photo: Task 1, with each field independently asserted. `noindex` default, lifted only for approved and content-complete: Task 2, defaulting to `False` and re-checked inside the renderer. Drafts and never-self-authored rows keeping `noindex`: covered by the payload — unauthored profiles yield empty fields, so `is_indexable` returns `False` without needing a separate rule. Host-aware `robots.txt`: Task 3. Practitioner sitemap bound to `PORTAL_BASE_URL`: Task 4.

**One spec line deliberately not implemented as written.** The spec says *"and the sample portal all keep it"*. The sample portal is served by a different route (`/sample`, `/sample/<slug>`) which this plan does not touch, so it keeps whatever it does today. If it does not currently carry `noindex`, that is a real gap — but fixing it belongs with that route, not smuggled into this one. **Check it and report:** `curl -sI https://myhealingoasis.com/sample | grep -i x-robots-tag`.

**Not in scope.** The duplicate `render_sitemap_xml` in `topic_render.py` and `mentor_render.py` stays duplicated. Consolidating three near-identical functions is a refactor with its own blast radius across two live surfaces, and doing it inside an indexing change would put unrelated risk on a hard-to-reverse deploy.
