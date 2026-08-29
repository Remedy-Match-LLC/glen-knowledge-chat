# Practitioner Page Server-Rendering Implementation Plan (Section 5a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the practitioner's name, tagline, bio, photo and share-preview metadata into the initial HTML response, so that a link texted by a client renders a real preview card instead of a blank one.

**Architecture:** A new `dashboard/practitioner_render.py` builds a complete HTML document from the payload `dashboard/public_surface.build_practitioner_storefront` already returns. The `/<slug>` route in `app.py` stops serving the static `practitioner-storefront.html` shell and returns the rendered document instead. The JSON API at `/api/p/<slug>` is unchanged and stays — the spec keeps it for portal-side surfaces. `noindex` stays on every response in this plan; lifting it is 5b.

**Tech Stack:** Python 3, Flask, `html.escape`, `json` for JSON-LD. No new dependencies. No template engine — the codebase's two existing server-rendered surfaces (`dashboard/mentor_render.py`, `dashboard/topic_render.py`) build HTML by string concatenation and this follows them.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md`, Section 5.

## Why this is split from indexing

The spec's Rollout section says: *"indexing last - indexing is the only step that is hard to walk back, since a page Google has crawled stays in the index after the page changes."* Server-rendering is fully reversible and delivers the referral payoff on its own. Indexing is plan 5b (`2026-08-29-practitioner-indexing.md`) and must not be started until this one is live and Glen has seen a real page.

## Global Constraints

Copied verbatim from the spec where the spec states a value:

- **Link-preview bots do not execute JavaScript at all.** Title, meta description, Open Graph and Twitter tags, `h1`, photo and bio must all be present in the initial HTML response.
- **`noindex` remains the default.** In this plan it stays on *every* response without exception.
- **Structured data: `Person` plus `ProfessionalService`.** Deliberately **not** `MedicalBusiness` or `Physician` — those assert a medical practice in machine-readable form, which is inaccurate for a health coach and is not a claim to emit from Remedy Match's domain.
- **Canonical tag** points at the practitioner's **canonical** slug at `https://myhealingoasis.com/<canonical-slug>`, collapsing `/p/<slug>`, every alternate slug, and any host duplication to one URL.
- **Assert on raw HTTP response bytes, not a rendered DOM**, for every server-rendering and meta-tag assertion. A browser-driven test would pass on the JavaScript path and hide the exact defect being fixed, because the bots that matter never run it.
- **Mutation-test every guard**, not just exercise it: plant the violation, confirm the test goes red, then remove it.
- The canonical host comes from `PORTAL_BASE_URL` (`https://myhealingoasis.com` in prod), **never** `PUBLIC_BASE_URL` (`https://illtowell.com`).

## Context the implementer needs

**The data is already there.** `dashboard/public_surface.build_practitioner_storefront(cx, slug)` returns exactly the payload this plan renders:

```python
{"slug", "practitioner_name", "practice_name", "bio", "photo_url", "logo_url",
 "services", "location", "accepting_clients", "featured_products",
 "catalog_url", "profit_disclosure", "tagline", "how_i_work"}
```

filtered through the `PRACTITIONER_PUBLIC_FIELDS` whitelist. `tagline` and `how_i_work` are already in that whitelist — verified 2026-08-29. It returns `None` for an unknown or unapproved slug.

The self-authored fields are gated on `profile_self_authored_at` being non-null in Postgres (see `dashboard/practitioner_profile.profile_for_slug`), so a scraped row that was never self-authored yields only name and practice name. **As of 2026-08-29 zero of the 23 portal practitioners are self-authored**, so every real page today renders name-only. Your tests must cover that state — it is the common case, not the edge case.

**Nothing in this codebase emits Open Graph or canonical tags today.** Grep confirms the only matches are scraped fixtures under `tests/fixtures/`. `dashboard/mentor_render.py:101 _document()` emits title, description, robots and viewport, and nothing else. You are adding a capability, not copying one.

**The route to change** is `practitioner_site` in `app.py` (find it with `grep -n 'def practitioner_site' app.py` — line numbers drift, anchor on content). Its tail currently reads:

```python
    resp = send_from_directory(STATIC, "practitioner-storefront.html")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.set_cookie("rm_ref", canonical, max_age=90 * 24 * 3600,
                    samesite="Lax", secure=request.is_secure)
    return resp
```

Everything above that tail — the host gate, `_public_surface_enabled()`, slug normalisation, the 301s for capitalised and alias slugs, the `record_view` call — is correct and must not change.

**Read `dashboard/mentor_render.py` before writing any code.** It is the house pattern: module-level `_FONTS`/`_STYLE`/`_BRANDBAR`/`_FOOTER` constants, an `_esc(s)` wrapper over `html.escape(..., quote=True)`, a `_document(...)` shell, and per-block builder functions that return `""` when their data is absent. Follow it.

---

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/practitioner_render.py` (new) | Pure functions: payload dict in, HTML string out. No database, no Flask, no environment reads. Every input arrives as an argument, which is what makes it testable without a request context. |
| `app.py` `practitioner_site` (modify) | Swap the static-file response for the rendered document. Nothing else in the route changes. |
| `tests/test_practitioner_render.py` (new) | Unit tests on the pure renderer: escaping, absent fields, JSON-LD shape, truncation. |
| `tests/test_practitioner_site_render.py` (new) | Route-level tests asserting on raw response bytes through the Flask test client. |
| `static/practitioner-storefront.html` (delete in Task 6) | Superseded. Deleted only after the route no longer references it. |

---

## Task 1: The document shell and escaping

**Files:**
- Create: `dashboard/practitioner_render.py`
- Test: `tests/test_practitioner_render.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_esc(s) -> str`, and `render_page_html(view, *, canonical_url) -> str`. `view` is the `build_practitioner_storefront` payload dict; `canonical_url` is a fully-qualified URL string. Tasks 2-5 extend this same function; do not rename it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_render.py
import pytest
from dashboard import practitioner_render as pr


def _view(**over):
    """Minimal payload: name only. This is what EVERY practitioner renders
    today, since zero of the 23 are self-authored."""
    v = {"slug": "mary-boyd", "practitioner_name": "Mary Boyd",
         "practice_name": "", "bio": "", "photo_url": "", "logo_url": "",
         "services": [], "location": "", "accepting_clients": True,
         "featured_products": [], "catalog_url": "/begin/explore",
         "profit_disclosure": "Your practitioner earns a portion of what you"
                              " spend here. Your price is the same either way.",
         "tagline": "", "how_i_work": ""}
    v.update(over)
    return v


def test_name_only_page_is_a_complete_document():
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<h1>Mary Boyd</h1>" in html
    assert "<title>Mary Boyd</title>" in html


def test_noindex_is_present_on_every_page():
    """Section 5a never lifts noindex. 5b owns that, behind a content bar."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '<meta name="robots" content="noindex">' in html


def test_markup_in_a_practitioner_name_is_escaped():
    html = pr.render_page_html(
        _view(practitioner_name='Mary <script>alert(1)</script> Boyd'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_quotes_in_an_attribute_value_are_escaped():
    """A raw double quote in a meta content= attribute breaks out of it."""
    html = pr.render_page_html(
        _view(tagline='She said "hello" and left'),
        canonical_url="https://myhealingoasis.com/mary-boyd")
    assert '"hello"' not in html
    assert "&quot;hello&quot;" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.practitioner_render'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# dashboard/practitioner_render.py
"""Server-rendered practitioner page.

Pure functions: payload dict in, HTML string out. No database access, no
Flask, no environment reads -- every input arrives as an argument. That is
what lets the whole surface be tested without a request context, and it is
why the canonical URL is passed in rather than derived here.

Why server-rendered at all: link-preview bots for iMessage, WhatsApp,
Facebook and Slack do not execute JavaScript. The JS storefront rendered a
blank preview card when a client texted their practitioner's link, which is
the referral motion this whole feature exists to serve.
"""
import html

# The page is deliberately plain. It inherits nothing from the funnel's
# stylesheet because it is a practitioner's page on their own domain.
_STYLE = (
    "<style>"
    ":root{--ink:#1F5A4D;--line:#e6e6e6;--muted:#666}"
    "body{font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;"
    "color:#222;margin:0}"
    ".wrap{max-width:720px;margin:0 auto;padding:32px 16px 64px}"
    "h1{color:var(--ink);font-size:30px;margin:0 0 4px}"
    ".tagline{font-size:19px;color:var(--muted);margin:0 0 20px}"
    ".practice{font-size:17px;margin:0 0 4px}"
    ".loc{color:var(--muted);margin:0 0 20px}"
    ".photo{width:160px;height:160px;border-radius:50%;object-fit:cover;"
    "display:block;margin:0 0 20px}"
    "section{border-top:1px solid var(--line);padding-top:20px;margin-top:24px}"
    "h2{font-size:18px;color:var(--ink);margin:0 0 8px}"
    "p{margin:0 0 12px}"
    ".disclosure{color:var(--muted);font-size:14px;margin-top:32px}"
    "</style>"
)


def _esc(s):
    """Escape for both text nodes and attribute values.

    quote=True is not optional here: a raw double quote in a tagline would
    otherwise terminate a meta content="..." attribute and let the rest of
    the value be parsed as markup.
    """
    return html.escape(str(s or ""), quote=True)


def render_page_html(view, *, canonical_url):
    """Render the complete document for one practitioner.

    `view` is the payload from public_surface.build_practitioner_storefront.
    `canonical_url` is fully qualified and built by the caller from
    PORTAL_BASE_URL -- never from PUBLIC_BASE_URL, which is the funnel.

    noindex is unconditional in section 5a. Section 5b introduces the content
    bar that decides when it may be lifted.
    """
    name = view.get("practitioner_name") or view.get("slug") or "Practitioner"
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(name)}</title>"
        '<meta name="robots" content="noindex">'
        f"{_STYLE}"
        "</head><body>"
        f'<div class="wrap"><h1>{_esc(name)}</h1></div>'
        "</body></html>"
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: PASS, 4 tests.

- [ ] **Step 5: Mutation-test the escaping guard**

Temporarily change `_esc` to `return str(s or "")`, re-run the tests, and confirm `test_markup_in_a_practitioner_name_is_escaped` and `test_quotes_in_an_attribute_value_are_escaped` both go **red**. Restore `_esc` and confirm green. Record both outcomes in your report — a guard you did not watch fail is a guard you have not tested.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py tests/test_practitioner_render.py
git commit -m "feat(practitioner): server-rendered document shell"
```

---

## Task 2: Title, meta description and the content blocks

**Files:**
- Modify: `dashboard/practitioner_render.py`
- Test: `tests/test_practitioner_render.py`

**Interfaces:**
- Consumes: `_esc`, `render_page_html(view, *, canonical_url)` from Task 1.
- Produces: `build_title(view) -> str` and `build_description(view) -> str`, both pure and both used again by Task 3 for the Open Graph tags. `MAX_DESCRIPTION = 200`.

**Why a shared description builder:** the meta description, `og:description` and `twitter:description` must not drift apart. One function, three call sites.

- [ ] **Step 1: Write the failing test**

```python
def test_title_pairs_name_with_practice_when_there_is_one():
    v = _view(practice_name="Fairbanks Wellness")
    assert pr.build_title(v) == "Mary Boyd — Fairbanks Wellness"


def test_title_is_just_the_name_when_there_is_no_practice():
    assert pr.build_title(_view()) == "Mary Boyd"


def test_description_prefers_the_tagline():
    v = _view(tagline="Helping nurses stop running on empty",
              bio="A much longer biography that should not win.")
    assert pr.build_description(v) == "Helping nurses stop running on empty"


def test_description_falls_back_to_the_bio():
    v = _view(bio="I work with nurses and shift workers.")
    assert pr.build_description(v) == "I work with nurses and shift workers."


def test_description_falls_back_to_a_neutral_line_when_empty():
    """Name-only is the common case today. An empty description tag is worse
    than a plain one: the preview card shows the raw URL instead."""
    assert pr.build_description(_view()) == "Mary Boyd on Remedy Match."


def test_description_is_truncated_on_a_word_boundary():
    v = _view(bio="word " * 100)
    d = pr.build_description(v)
    assert len(d) <= pr.MAX_DESCRIPTION
    assert d.endswith("…")
    assert not d.endswith(" …")


def test_bio_paragraphs_survive_as_separate_paragraphs():
    """how_i_work and bio are stored with blank lines preserved on purpose --
    flattening them was a data-destroying defect fixed in section 2b."""
    v = _view(bio="First para.\n\nSecond para.")
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<p>First para.</p>" in html
    assert "<p>Second para.</p>" in html


def test_absent_blocks_emit_nothing():
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" not in html
    assert "<h2>How I work</h2>" not in html
    assert "<img" not in html


def test_present_blocks_are_labelled():
    v = _view(bio="About me.", how_i_work="My approach.", location="Fairbanks, AK",
              practice_name="Fairbanks Wellness", tagline="A tagline")
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "<h2>About</h2>" in html
    assert "<h2>How I work</h2>" in html
    assert "Fairbanks, AK" in html
    assert "Fairbanks Wellness" in html
    assert "A tagline" in html


def test_the_profit_disclosure_is_always_rendered():
    """It is a disclosure. It does not get to be conditional."""
    html = pr.render_page_html(_view(), canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Your practitioner earns a portion of what you spend here." in html


def test_the_remaining_public_fields_render():
    """logo_url, services, accepting_clients and featured_products had no
    renderer in the JS shell. Covered here per-block; the whole-whitelist
    sweep lives in tests/test_public_surface_routes.py (Task 6) so there is
    exactly one place that knows the full field list."""
    v = _view(logo_url="https://cdn.example/logo.jpg",
              services=["sleep coaching"],
              featured_products=[{"name": "SentinelProduct", "price": "$10"}])
    html = pr.render_page_html(v, canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "logo.jpg" in html
    assert "sleep coaching" in html
    assert "SentinelProduct" in html


def test_not_accepting_clients_says_so_rather_than_going_silent():
    """A False value is information. Rendering nothing would read as 'unknown'
    to a visitor deciding whether to reach out."""
    html = pr.render_page_html(_view(accepting_clients=False),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "Not currently accepting new clients" in html


def test_services_render_as_a_list():
    html = pr.render_page_html(_view(services=["sleep coaching", "nutrition"]),
                               canonical_url="https://myhealingoasis.com/mary-boyd")
    assert "sleep coaching" in html and "nutrition" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: FAIL with `AttributeError: module 'dashboard.practitioner_render' has no attribute 'build_title'`.

- [ ] **Step 3: Write the implementation**

Add to `dashboard/practitioner_render.py`:

```python
MAX_DESCRIPTION = 200


def build_title(view):
    """Name, or "Name — Practice" when a practice name exists.

    An em dash separator, not a pipe: this is a person's page, not a
    directory listing.
    """
    name = view.get("practitioner_name") or view.get("slug") or "Practitioner"
    practice = (view.get("practice_name") or "").strip()
    return f"{name} — {practice}" if practice else str(name)


def _truncate(text, limit=MAX_DESCRIPTION):
    """Cut on a word boundary and mark the cut.

    A preview card cut mid-word reads as broken; the ellipsis is what tells a
    reader the sentence continues on the page.
    """
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut + "…"


def build_description(view):
    """Tagline, else bio, else a neutral line naming the practitioner.

    Never empty: an absent description makes the preview card fall back to
    showing the bare URL, which is the blank-card problem this plan exists to
    fix, only quieter.
    """
    name = view.get("practitioner_name") or view.get("slug") or "This practitioner"
    for key in ("tagline", "bio"):
        val = (view.get(key) or "").strip()
        if val:
            return _truncate(val)
    return f"{name} on Remedy Match."


def _paragraphs(text):
    """Blank-line-separated paragraphs, preserved.

    save_draft deliberately keeps the blank lines in bio and how_i_work.
    Flattening them here would undo that at the last possible moment.
    """
    out = []
    for para in str(text or "").split("\n\n"):
        para = para.strip()
        if para:
            out.append(f"<p>{_esc(para)}</p>")
    return "".join(out)


def _section(heading, text):
    """A labelled block, or nothing at all when the field is empty."""
    body = _paragraphs(text)
    return f"<section><h2>{_esc(heading)}</h2>{body}</section>" if body else ""


def _photo(view):
    url = (view.get("photo_url") or "").strip()
    if not url:
        return ""
    alt = _esc(view.get("practitioner_name") or "Practitioner")
    return f'<img class="photo" src="{_esc(url)}" alt="{alt}">'


def _line(css_class, text):
    text = (text or "").strip()
    return f'<p class="{css_class}">{_esc(text)}</p>' if text else ""


def _logo(view):
    url = (view.get("logo_url") or "").strip()
    if not url:
        return ""
    practice = view.get("practice_name") or view.get("practitioner_name") or ""
    return f'<img class="logo" src="{_esc(url)}" alt="{_esc(practice)}">'


def _services(view):
    items = [str(s).strip() for s in (view.get("services") or []) if str(s).strip()]
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(s)}</li>" for s in items)
    return f"<section><h2>Services</h2><ul>{lis}</ul></section>"


def _accepting(view):
    """Render the answer either way.

    False is information a visitor needs before they compose an email. Showing
    nothing reads as "unknown", which is the one thing it is not.
    """
    return ('<p class="accepting">Accepting new clients</p>'
            if view.get("accepting_clients")
            else '<p class="accepting">Not currently accepting new clients</p>')


def _featured(view):
    """Retail prices only -- the payload whitelist guarantees that upstream.

    Always empty today: build_practitioner_storefront hardcodes [] and no
    profile supplies it. Rendered anyway so the field is not one more thing
    that reaches the payload and stops there.
    """
    items = view.get("featured_products") or []
    lis = []
    for p in items:
        if isinstance(p, dict):
            name = str(p.get("name") or "").strip()
            price = str(p.get("price") or "").strip()
        else:
            name, price = str(p).strip(), ""
        if name:
            lis.append(f"<li>{_esc(name)}"
                       + (f" <span class=\"price\">{_esc(price)}</span>" if price else "")
                       + "</li>")
    if not lis:
        return ""
    return f"<section><h2>Featured</h2><ul>{''.join(lis)}</ul></section>"
```

Then replace the body of `render_page_html` (keep the signature and the docstring):

```python
def render_page_html(view, *, canonical_url):
    name = view.get("practitioner_name") or view.get("slug") or "Practitioner"
    title = build_title(view)
    desc = build_description(view)
    body = (
        '<div class="wrap">'
        + _photo(view)
        + f"<h1>{_esc(name)}</h1>"
        + _line("tagline", view.get("tagline"))
        + _line("practice", view.get("practice_name"))
        + _logo(view)
        + _line("loc", view.get("location"))
        + _accepting(view)
        + _section("About", view.get("bio"))
        + _section("How I work", view.get("how_i_work"))
        + _services(view)
        + _featured(view)
        + f'<p><a href="{_esc(view.get("catalog_url") or "/begin/explore")}">'
          "Browse the full catalog</a></p>"
        + f'<p class="disclosure">{_esc(view.get("profit_disclosure"))}</p>'
        + "</div>"
    )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f'<meta name="description" content="{_esc(desc)}">'
        '<meta name="robots" content="noindex">'
        f"{_STYLE}"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )
```

Note `test_name_only_page_is_a_complete_document` asserts `<title>Mary Boyd</title>`; with no practice name `build_title` returns exactly that, so it still passes.

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: PASS, 14 tests.

- [ ] **Step 5: Mutation-test the paragraph guard**

Change `_paragraphs` to `return f"<p>{_esc(' '.join(str(text or '').split()))}</p>"` (the flattening behaviour section 2b removed). Confirm `test_bio_paragraphs_survive_as_separate_paragraphs` goes **red**. Restore and confirm green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py tests/test_practitioner_render.py
git commit -m "feat(practitioner): title, description and content blocks"
```

---

## Task 3: Open Graph, Twitter Card and canonical

**Files:**
- Modify: `dashboard/practitioner_render.py`
- Test: `tests/test_practitioner_render.py`

**Interfaces:**
- Consumes: `build_title`, `build_description`, `_esc` from Task 2.
- Produces: no new public function — the tags are emitted inside `render_page_html`. Task 4 passes `canonical_url` in; this task is what finally uses it.

**This is the task the whole plan exists for.** Everything before it is scaffolding.

- [ ] **Step 1: Write the failing test**

```python
CANON = "https://myhealingoasis.com/mary-boyd"


def test_open_graph_tags_are_present():
    v = _view(tagline="Helping nurses stop running on empty",
              practice_name="Fairbanks Wellness")
    html = pr.render_page_html(v, canonical_url=CANON)
    assert '<meta property="og:type" content="profile">' in html
    assert '<meta property="og:title" content="Mary Boyd — Fairbanks Wellness">' in html
    assert ('<meta property="og:description" content="Helping nurses stop '
            'running on empty">') in html
    assert f'<meta property="og:url" content="{CANON}">' in html
    assert '<meta property="og:site_name" content="Remedy Match">' in html


def test_og_image_is_present_only_when_there_is_a_photo():
    assert 'property="og:image"' not in pr.render_page_html(_view(), canonical_url=CANON)
    v = _view(photo_url="https://cdn.example/mary.jpg")
    html = pr.render_page_html(v, canonical_url=CANON)
    assert '<meta property="og:image" content="https://cdn.example/mary.jpg">' in html


def test_twitter_card_type_follows_the_photo():
    """summary_large_image with no image renders as an empty box."""
    assert ('<meta name="twitter:card" content="summary">'
            in pr.render_page_html(_view(), canonical_url=CANON))
    v = _view(photo_url="https://cdn.example/mary.jpg")
    assert ('<meta name="twitter:card" content="summary_large_image">'
            in pr.render_page_html(v, canonical_url=CANON))


def test_canonical_link_uses_the_url_it_was_given():
    html = pr.render_page_html(_view(), canonical_url=CANON)
    assert f'<link rel="canonical" href="{CANON}">' in html


def test_og_and_meta_descriptions_agree():
    """Three descriptions from one builder. They must not drift."""
    v = _view(bio="I work with nurses and shift workers.")
    html = pr.render_page_html(v, canonical_url=CANON)
    d = pr.build_description(v)
    assert f'<meta name="description" content="{d}">' in html
    assert f'<meta property="og:description" content="{d}">' in html
    assert f'<meta name="twitter:description" content="{d}">' in html


def test_a_quote_in_the_canonical_url_cannot_break_the_attribute():
    html = pr.render_page_html(_view(), canonical_url='https://x/"><script>')
    assert '"><script>' not in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: FAIL — `assert '<meta property="og:type" content="profile">' in html`.

- [ ] **Step 3: Write the implementation**

Add to `dashboard/practitioner_render.py`:

```python
SITE_NAME = "Remedy Match"


def _share_tags(view, title, desc, canonical_url):
    """Open Graph and Twitter Card tags.

    og:type is "profile" rather than "website" because this page is a person.
    The Twitter card type follows the photo: summary_large_image with no image
    renders as an empty grey box, which looks more broken than the small card.
    """
    photo = (view.get("photo_url") or "").strip()
    tags = [
        '<meta property="og:type" content="profile">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
        f'<meta property="og:url" content="{_esc(canonical_url)}">',
        f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">',
        f'<meta name="twitter:card" content='
        f'"{"summary_large_image" if photo else "summary"}">',
        f'<meta name="twitter:title" content="{_esc(title)}">',
        f'<meta name="twitter:description" content="{_esc(desc)}">',
    ]
    if photo:
        tags.append(f'<meta property="og:image" content="{_esc(photo)}">')
        tags.append(f'<meta name="twitter:image" content="{_esc(photo)}">')
    return "".join(tags)
```

In `render_page_html`, insert after the `description` meta and before the robots meta:

```python
        f"{_share_tags(view, title, desc, canonical_url)}"
        f'<link rel="canonical" href="{_esc(canonical_url)}">'
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: PASS, 20 tests.

- [ ] **Step 5: Mutation-test the card-type guard**

Change the card type to a bare `"summary_large_image"` regardless of photo. Confirm `test_twitter_card_type_follows_the_photo` goes **red**. Restore, confirm green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py tests/test_practitioner_render.py
git commit -m "feat(practitioner): Open Graph, Twitter Card and canonical tags"
```

---

## Task 4: JSON-LD, Person plus ProfessionalService

**Files:**
- Modify: `dashboard/practitioner_render.py`
- Test: `tests/test_practitioner_render.py`

**Interfaces:**
- Consumes: `build_description`, `_esc` from Task 2.
- Produces: `build_jsonld(view, canonical_url) -> list` — a list of two schema.org dicts, emitted as one `<script type="application/ld+json">` containing a JSON array.

**Read the constraint before you write this.** The spec: *"Deliberately not `MedicalBusiness` or `Physician`. Those schema types assert a medical practice to Google in machine-readable form. For a health coach that is inaccurate and it is not a claim to emit from Remedy Match's domain."* Mary Boyd is an RN working as a health coach. Do not add a medical type, a `medicalSpecialty` field, or anything that reads as a clinical claim, no matter how well it would score.

- [ ] **Step 1: Write the failing test**

```python
import json
import re


def _jsonld(html_str):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                  html_str, re.S)
    assert m, "no JSON-LD block"
    return json.loads(m.group(1))


def test_jsonld_is_person_plus_professional_service():
    data = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))
    assert [d["@type"] for d in data] == ["Person", "ProfessionalService"]


def test_jsonld_never_asserts_a_medical_practice():
    """Spec constraint: not MedicalBusiness, not Physician, no specialty."""
    v = _view(bio="RN and health coach", practice_name="Fairbanks Wellness",
              services=["health coaching", "nutrition"])
    raw = pr.render_page_html(v, canonical_url=CANON)
    for banned in ("MedicalBusiness", "Physician", "medicalSpecialty",
                   "MedicalClinic", "Hospital"):
        assert banned not in raw


def test_jsonld_carries_name_url_and_description():
    v = _view(tagline="Helping nurses stop running on empty")
    person = _jsonld(pr.render_page_html(v, canonical_url=CANON))[0]
    assert person["name"] == "Mary Boyd"
    assert person["url"] == CANON
    assert person["description"] == "Helping nurses stop running on empty"


def test_jsonld_omits_absent_fields_rather_than_emitting_empty_ones():
    person = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))[0]
    assert "image" not in person
    assert "address" not in _jsonld(pr.render_page_html(_view(), canonical_url=CANON))[1]


def test_jsonld_includes_photo_and_location_when_present():
    v = _view(photo_url="https://cdn.example/mary.jpg", location="Fairbanks, AK",
              practice_name="Fairbanks Wellness", services=["health coaching"])
    data = _jsonld(pr.render_page_html(v, canonical_url=CANON))
    assert data[0]["image"] == "https://cdn.example/mary.jpg"
    assert data[1]["name"] == "Fairbanks Wellness"
    assert data[1]["address"] == "Fairbanks, AK"
    assert data[1]["serviceType"] == ["health coaching"]


def test_professional_service_falls_back_to_the_person_name():
    """A coach practising under their own name still gets a service entity."""
    data = _jsonld(pr.render_page_html(_view(), canonical_url=CANON))
    assert data[1]["name"] == "Mary Boyd"


def test_a_closing_script_tag_in_the_data_cannot_break_out():
    v = _view(bio="</script><script>alert(1)</script>")
    raw = pr.render_page_html(v, canonical_url=CANON)
    assert "</script><script>alert(1)" not in raw
    _jsonld(raw)  # must still parse
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: FAIL with `AssertionError: no JSON-LD block`.

- [ ] **Step 3: Write the implementation**

Add to `dashboard/practitioner_render.py` (add `import json` at the top):

```python
def build_jsonld(view, canonical_url):
    """Person plus ProfessionalService.

    Deliberately NOT MedicalBusiness or Physician. Those schema types assert a
    medical practice to Google in machine-readable form. Most practitioners
    here are health coaches, for whom that is inaccurate, and it is not a
    claim to emit from this domain on their behalf. Same reasoning as the
    credential-verification decision in section 1 of the spec.

    Fields are omitted when absent rather than emitted empty: an empty
    schema.org value is a worse signal than a missing one.
    """
    name = view.get("practitioner_name") or view.get("slug") or "Practitioner"
    person = {"@context": "https://schema.org", "@type": "Person",
              "name": name, "url": canonical_url,
              "description": build_description(view)}
    if (view.get("photo_url") or "").strip():
        person["image"] = view["photo_url"].strip()

    service = {"@context": "https://schema.org", "@type": "ProfessionalService",
               "name": (view.get("practice_name") or "").strip() or name,
               "url": canonical_url}
    if (view.get("location") or "").strip():
        service["address"] = view["location"].strip()
    services = [str(s).strip() for s in (view.get("services") or []) if str(s).strip()]
    if services:
        service["serviceType"] = services
    return [person, service]


def _jsonld_tag(view, canonical_url):
    """Serialise the JSON-LD, neutralising any embedded closing tag.

    Escaping `</` is the standard defence: inside a <script> block the HTML
    parser looks for the literal characters `</script`, and it does so BEFORE
    any JSON parsing happens, so a bio containing one would end the script
    early. `<\\/` is valid JSON for the same string.
    """
    raw = json.dumps(build_jsonld(view, canonical_url), ensure_ascii=False)
    return ('<script type="application/ld+json">'
            + raw.replace("</", "<\\/") + "</script>")
```

In `render_page_html`, insert immediately before `{_STYLE}`:

```python
        f"{_jsonld_tag(view, canonical_url)}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py -q -p no:randomly`

Expected: PASS, 27 tests.

- [ ] **Step 5: Mutation-test the script-breakout guard**

Remove the `.replace("</", "<\\/")`. Confirm `test_a_closing_script_tag_in_the_data_cannot_break_out` goes **red**. Restore, confirm green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/practitioner_render.py tests/test_practitioner_render.py
git commit -m "feat(practitioner): Person and ProfessionalService JSON-LD"
```

---

## Task 5: Wire BOTH routes

**Files:**
- Modify: `app.py`, functions `practitioner_site` and `practitioner_storefront` (locate with `grep -n 'def practitioner_site\|def practitioner_storefront' app.py`)
- Test: `tests/test_practitioner_site_render.py` (new)

**Interfaces:**
- Consumes: `dashboard.practitioner_render.render_page_html(view, *, canonical_url)` from Tasks 1-4; `dashboard.public_surface.build_practitioner_storefront(cx, slug)`, which already exists and returns the payload or `None`.
- Produces: `_render_practitioner_page(view, canonical_slug)` — a helper in `app.py` returning a configured `Response`, used by both routes. Task 6 depends on both routes being converted.

**TWO routes serve the JS shell, not one.** `grep -n "practitioner-storefront" app.py` returns two hits:

- `practitioner_site` → `/<slug>`, portal host, the canonical page.
- `practitioner_storefront` → `/p/<slug>`. On the portal host it 301s to `/<slug>`, but **on the funnel host it serves the shell directly** and is a live public URL.

Converting only the first would leave the blank-preview bug alive on every `illtowell.com/p/<slug>` link ever texted or printed — and Task 6 deletes the file both routes reference, which would 500 the one you skipped. Convert both.

**Do not touch** the host gates, `_public_surface_enabled()`, slug normalisation, the 301 redirects, or the `record_view` calls in either route. Only the response-building tails change.

**Trap: `_ps` means two different modules in these two adjacent functions.** In `practitioner_storefront` it is `from dashboard import public_surface as _ps`. In `practitioner_site` it is `from dashboard import practitioner_slugs as _ps`. Both are function-local imports, so both are correct in place — but do not copy a line between the two routes assuming `_ps` is the same thing. The code below uses each route's own alias deliberately.

**Both pages get the same canonical**, pointing at `PORTAL_BASE_URL/<slug>`. That is the spec's requirement verbatim: the canonical *"collapses the legacy `/p/<slug>` path, every alternate slug, and any host duplication to one URL"*. A funnel-host page declaring a portal-host canonical is exactly what collapses the duplicate.

**The canonical URL must be built from `PORTAL_BASE_URL`.** The spec calls this out because the two existing sitemaps hardcode `PUBLIC_BASE_URL`, and copying that pattern would emit `https://illtowell.com/mary-boyd` — a URL that does not serve this page. `PORTAL_BASE_URL` is `https://myhealingoasis.com` in prod.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_site_render.py
"""Route-level tests for the server-rendered practitioner page.

Every assertion here is on RAW RESPONSE BYTES, never a parsed DOM. The bots
this feature exists for -- iMessage, WhatsApp, Facebook, Slack -- do not
execute JavaScript, so a DOM-based test would pass on the old JS path and
hide the exact defect being fixed.
"""
import os
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
    monkeypatch.setattr(_ps, "resolve", lambda cx, s: ("canonical", s))
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)
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


def test_a_payload_failure_degrades_to_404_not_500(portal, monkeypatch):
    """This catch-all answers every bot probe of /admin, /.env and /wordpress.
    One broken read must not become a site-wide 500."""
    from dashboard import public_surface as _psurf

    def boom(cx, slug):
        raise RuntimeError("postgres is down")
    monkeypatch.setattr(_psurf, "build_practitioner_storefront", boom)
    assert portal.get("/mary-boyd").status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_site_render.py -q -p no:randomly`

Expected: FAIL — the static shell has no `<h1>Mary Boyd</h1>`; the JS fills it in at runtime.

- [ ] **Step 3: Write the shared helper**

Add to `app.py`, immediately above `practitioner_site`:

```python
def _render_practitioner_page(view, canonical_slug):
    """Build the server-rendered response both practitioner routes return.

    One helper, two callers, because a difference between the canonical page
    and its legacy /p/ alias is a bug in every case -- including the meta tags
    a preview bot reads, which is the whole point of rendering server-side.

    The canonical always points at PORTAL_BASE_URL, even when this response is
    served from the funnel host. That is what collapses the legacy path and
    the host duplication onto one URL, per the spec. PUBLIC_BASE_URL here
    would be the bug: the two existing sitemaps hardcode the funnel host, and
    copying that pattern would declare a canonical that does not serve this
    page.
    """
    from dashboard import practitioner_render as _prender
    portal_base = (os.environ.get("PORTAL_BASE_URL") or "").rstrip("/")
    resp = Response(
        _prender.render_page_html(
            view, canonical_url=f"{portal_base}/{canonical_slug}"),
        mimetype="text/html")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.set_cookie("rm_ref", canonical_slug, max_age=90 * 24 * 3600,
                    samesite="Lax", secure=request.is_secure)
    return resp
```

- [ ] **Step 4: Convert `practitioner_site` (`/<slug>`, portal host)**

Replace only its tail, from `resp = send_from_directory(...)` to `return resp`:

```python
    # Server-rendered, not the JS shell: link-preview bots for iMessage,
    # WhatsApp, Facebook and Slack do not execute JavaScript, so the old
    # storefront produced a blank preview card when a client texted this link.
    # /api/p/<slug> is unchanged and still serves portal-side callers.
    #
    # Fail closed for the same reason the resolve above does: this catch-all
    # answers every unmatched root path on the portal host, including every
    # bot probe of /admin, /.env and /wordpress. A broken payload read must
    # degrade to "no such slug", not turn one fault into a site-wide 500.
    try:
        from dashboard import public_surface as _psurf2
        with db.connect(LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            view = _psurf2.build_practitioner_storefront(cx, canonical)
    except Exception as e:  # noqa: BLE001
        print(f"[practitioner_site] payload failed for {canonical!r}: {e!r}",
              flush=True)
        return ("", 404)
    if not view:
        return ("", 404)
    return _render_practitioner_page(view, canonical)
```

- [ ] **Step 5: Convert `practitioner_storefront` (`/p/<slug>`, funnel host)**

This route already fetches the payload to decide 404-or-not, then throws it away and serves the shell. Reuse it instead of reading twice. Its current tail reads:

```python
    with db.connect(LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        if not _ps.build_practitioner_storefront(cx, slug):
            return ("", 404)
    # Record the view for this approved affiliate
    try:
        with _db_lock, db.connect(LOG_DB) as _cx:
            _ps.record_view(_cx, slug, "storefront")
    except Exception:
        pass  # instrumentation must never break the page
    resp = send_from_directory(STATIC, "practitioner-storefront.html")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.set_cookie("rm_ref", slug, max_age=90 * 24 * 3600,
                    samesite="Lax", secure=request.is_secure)
    return resp
```

Replace it with:

```python
    # Keep the payload the existence check already fetched -- two reads of the
    # same row on a public page is a wasted round trip, and a second read can
    # disagree with the first.
    try:
        with db.connect(LOG_DB) as cx:
            cx.row_factory = sqlite3.Row
            view = _ps.build_practitioner_storefront(cx, slug)
    except Exception as e:  # noqa: BLE001
        print(f"[practitioner_storefront] payload failed for {slug!r}: {e!r}",
              flush=True)
        return ("", 404)
    if not view:
        return ("", 404)
    # Record the view for this approved affiliate
    try:
        with _db_lock, db.connect(LOG_DB) as _cx:
            _ps.record_view(_cx, slug, "storefront")
    except Exception:
        pass  # instrumentation must never break the page
    # Same renderer, same canonical target as /<slug>. This page is the legacy
    # alias; its canonical points at the portal host, which is what collapses
    # the duplicate rather than competing with it.
    return _render_practitioner_page(view, slug)
```

- [ ] **Step 6: Add the funnel-route test**

Append to `tests/test_practitioner_site_render.py`:

```python
def test_the_legacy_funnel_path_is_also_server_rendered(monkeypatch):
    """/p/<slug> on the funnel host is a live public URL — every one ever
    texted or printed still resolves there. Leaving it on the JS shell would
    keep the blank-preview bug alive on exactly those links."""
    monkeypatch.setattr(appmod, "_on_portal_host", lambda: False)
    monkeypatch.setattr(appmod, "_public_surface_enabled", lambda: True)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://myhealingoasis.com")
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
    from dashboard import public_surface as _psurf
    monkeypatch.setattr(_psurf, "build_practitioner_storefront",
                        lambda cx, slug: dict(VIEW, slug=slug))
    monkeypatch.setattr(_psurf, "record_view", lambda cx, slug, kind: None)

    body = appmod.app.test_client().get("/p/mary-boyd").get_data(as_text=True)
    assert '<link rel="canonical" href="https://myhealingoasis.com/mary-boyd">' in body
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_site_render.py -q -p no:randomly`

Expected: PASS, 10 tests.

- [ ] **Step 8: Mutation-test the canonical-host guard**

Change `portal_base` in `_render_practitioner_page` to read `PUBLIC_BASE_URL`. Confirm both `test_canonical_binds_to_the_portal_host_not_the_funnel` and `test_the_legacy_path_declares_the_portal_canonical` go **red**. Restore, confirm green.

- [ ] **Step 9: Run the wider suite**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner*.py tests/test_slug_route_collision.py tests/test_public_surface*.py -q -p no:randomly`

Two failures are expected and are **not yours** — both reproduce on unmodified `origin/main`, verified 2026-08-29:
- `tests/test_practitioner_personal_order.py::test_personal_card_return_books_one_sales_receipt`
- `tests/test_membership.py::test_studio_credit_post_inserts_intent_sends_glen_notification`

Any *other* failure is yours. If you are unsure, check it out on a clean `origin/main` worktree and run it there before claiming it is pre-existing.

- [ ] **Step 10: Commit**

```bash
git add app.py tests/test_practitioner_site_render.py
git commit -m "feat(practitioner): server-render both /<slug> and /p/<slug>"
```

---

## Task 6: Retire the JS shell and collect the waiting guard

**Files:**
- Modify: `tests/test_public_surface_routes.py` (two tests, around lines 327-365 — anchor on the test names, not the numbers)
- Delete: `static/practitioner-storefront.html`
- Test: `tests/test_practitioner_site_render.py`

**Interfaces:**
- Consumes: both routes converted in Task 5; `render_page_html` from Tasks 1-4.
- Produces: nothing.

**A previous session left a test waiting for this plan.** `tests/test_public_surface_routes.py` contains `test_every_public_field_is_actually_rendered_somewhere`, marked `@pytest.mark.xfail(strict=False)` with the reason *"Expected to fail until section 5 server-renders the storefront"* and the instruction *"When section 5 lands, this should go XPASS. At that point delete the xfail marker and let it be an ordinary guard."*

That is this plan. Collect it.

Both that test and its companion `test_the_render_guard_is_measuring_the_right_file` read `static/practitioner-storefront.html` from disk. Deleting the file breaks them. **Re-point them at the renderer; do not delete them.** They encode a real rule — a field can reach the payload and be rendered by nothing — and that rule outlives the file it was written against.

Note the oracle has to change too. The old test searched for field *names* (`"practitioner_name" in html`) because the JS shell referenced `v.practitioner_name` in its source. A server-rendered page contains **values, not field names**, so the replacement uses a sentinel value per field. That is the same check, correctly aimed.

- [ ] **Step 1: Verify only tests still reference the file**

```bash
grep -rn "practitioner-storefront" --include='*.py' --include='*.html' --include='*.js' . | grep -v '^./tests/'
```

Expected: **no output** — Task 5 converted both routes. `dashboard/practitioner_profile.py` mentions a *spec filename* containing that string in a comment; that is not a reference and needs no change. If any `app.py` hit remains, stop and report it: a route still serving the file would 500 the moment you delete it.

- [ ] **Step 2: Re-point the two guard tests**

In `tests/test_public_surface_routes.py`, replace both tests with:

```python
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
    """
    from dashboard import public_surface as ps
    from dashboard import practitioner_render as pr
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
    }
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
                "profit_disclosure": "SentinelDisclosure"}
    missing = sorted(f for f, s in expected.items() if s not in html)
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
```

- [ ] **Step 3: Run them to confirm the guard now genuinely passes**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_public_surface_routes.py -q -p no:randomly`

Expected: PASS with **no XPASS and no xfail** — the marker is gone and the test stands on its own. If `test_every_public_field_is_actually_rendered_somewhere` fails, a field in the whitelist is genuinely unrendered: go back to Task 2 and render it. Do **not** relax the assertion.

- [ ] **Step 4: Write the deletion guard**

Append to `tests/test_practitioner_site_render.py`:

```python
def test_the_js_shell_is_gone():
    """The blank-preview-card bug lived in this file, and both public routes
    used to serve it. If a future change re-introduces it, the regression
    comes back silently — a browser would still look right."""
    import pathlib
    assert not (pathlib.Path(appmod.STATIC) / "practitioner-storefront.html").exists()
```

- [ ] **Step 5: Run it to verify it fails**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_site_render.py::test_the_js_shell_is_gone -q -p no:randomly`

Expected: FAIL — the file still exists.

- [ ] **Step 6: Delete the file**

```bash
git rm static/practitioner-storefront.html
```

- [ ] **Step 7: Run the full affected set**

Run: `doppler run -p remedy-match -c dev -- python3 -m pytest tests/test_practitioner_render.py tests/test_practitioner_site_render.py tests/test_public_surface_routes.py tests/test_practitioner_site_routes.py -q -p no:randomly`

Expected: PASS. `tests/test_practitioner_site_routes.py:58` carries a comment about the shell re-deriving its key from `location.pathname`; if that test asserts on shell behaviour rather than mentioning it in prose, invert it the same way — the 301-to-lowercase behaviour it guards is still correct and must keep its test.

- [ ] **Step 8: Commit**

```bash
git add -A static/practitioner-storefront.html tests/test_practitioner_site_render.py tests/test_public_surface_routes.py
git commit -m "chore(practitioner): retire the JS shell, collect the render guard"
```

---

## Verification before merge

Server-rendering is only proven by looking at bytes on the wire. After the deploy lands:

```bash
curl -s https://myhealingoasis.com/mary-boyd | grep -E 'og:|twitter:|canonical|<h1|<title'
```

Every tag must be present in that raw output, with **no JavaScript executed**. A page that looks right in a browser but shows nothing here has not been fixed — the browser ran the JS and the bot will not.

Then paste the URL into an iMessage or Slack draft and confirm a real preview card renders. That is the actual acceptance criterion; the tests are a proxy for it.

Expect it to be sparse. Mary is currently name-only, and until she writes her page the card will show her name and the neutral fallback description. That is correct behaviour, not a defect.

---

## Self-review notes

**Spec coverage for Section 5.** Server-rendering: Tasks 1-5. Title/meta/OG/Twitter/h1/photo/bio in initial HTML: Tasks 2-3. Canonical collapsing alternates and hosts: Task 3 plus the existing 301s in the route, which Task 5 preserves. `Person` + `ProfessionalService`, not medical types: Task 4. JSON API retained: untouched by design, asserted in Task 5's fixture. `noindex` default: asserted in Tasks 1 and 5.

**Deferred to plan 5b** (`2026-08-29-practitioner-indexing.md`), per the spec's own "indexing last" instruction: separating `live` from `indexed`, the content-completeness bar, host-aware `robots.txt`, the practitioner sitemap, and lifting `noindex`. No task here touches any of them.

**Known follow-up, not in scope.** `build_practitioner_storefront` reads `practice_name` from `affiliate_signups.organization` as its base value, and Glen confirmed on 2026-08-28 that `organization` is a different field from `practitioners.practice_name`. The self-authored profile overlay corrects it whenever a practitioner has authored one, so the rendered page is right for any practitioner this plan actually showcases. For never-self-authored rows the page may show an `organization` value as a practice name. Flagging rather than fixing: changing it touches the payload every existing caller reads, which is its own change with its own blast radius.
