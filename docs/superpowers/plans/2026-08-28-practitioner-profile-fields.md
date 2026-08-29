# Practitioner Profile Fields — tagline, how_i_work, and two save-path bugs (Section 2b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a practitioner two new self-authored fields — a short tagline and a longer "how I work" — through the existing review gate, and fix the two defects in the save path that section 2a deliberately left alone.

**Architecture:** Both new fields are ordinary draft fields: sanitized in `save_draft`, stored in the draft's `fields` JSON, policied `review`, and published by `_write_live_profile` like every other column. The two bug fixes are `logo_url` (a column that exists and is publicly whitelisted but is written by nothing) and `photo_url` (accepted as an unvalidated raw string).

**Tech Stack:** Python 3, Flask, Postgres via `db_supabase.supabase_cursor`, sqlite (LOG_DB) via `dashboard.db`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md` (section 2)

**Depends on:** section 2a (`sess/a191e6ab-s2`, PR #1481). This branch is stacked on it — the draft store, the review gate, and the guard tests all come from there.

## Global Constraints

- **`_write_live_profile` stays the ONLY function that stamps `profile_self_authored_at`.** `tests/test_practitioner_profile.py::test_only_one_function_stamps_profile_self_authored_at` enforces it. Do not add a second writer.
- **Publish replaces the row wholesale.** Every column in `_write_live_profile`'s SET list is written from `fields.get(k, <default>)`, so a draft missing a key BLANKS the live value. **Any field added to one side must be added to the other in the same commit** — `test_save_draft_writes_every_field_publish_reads` derives the published set from the function's own source and goes red otherwise. That test is your safety net; do not weaken it.
- **The migration must NOT re-create `v_practitioners_public`.** That view already exposes `bio`, `photo_url`, `credentials`, `city`, `state` directly to the public practitioner finder, bypassing `PRACTITIONER_PUBLIC_FIELDS`. Adding `tagline`/`how_i_work` to it would publish practitioner prose with no whitelist in front of it. `migrations/practitioners-storefront.sql` sets the precedent and says so explicitly — follow it.
- **Adding a key to `PRACTITIONER_PUBLIC_FIELDS` is a privacy decision, not a refactor.** `dashboard/public_surface.py` says the whitelist fails closed by design.
- **Drafts are sqlite (`?`), the live profile is Postgres (`%s`).** Never both in one function.
- **Stage named files only.** NEVER `git add -A` or `git add .` in this shared checkout.
- **Never run the full test suite.** Run only named files.
- Work in `/tmp/wt-deploy-chat-a191e6ab`, branch `sess/a191e6ab-s2b`.

## Scope boundary

**In scope:** `tagline`, `how_i_work`, the `logo_url` dead write, `photo_url` validation, and the settings-page inputs for all three.

**Out of scope, deliberately:**
- **`credentials` moved to plan 2e.** `practitioners.credentials` already exists as SCRAPED free text, production regexes classify professions from it (`OTR/L|OTD|MOT|MSOT`), and it is exposed in `v_practitioners_public`. Letting a practitioner self-author into it would clobber data the profession tagging depends on and publish a licensure claim with no verification. It belongs with the credential-verification workflow 2e already owns.
- **`seo_title` / `seo_description` moved to section 5.** Their only consumer is the server-rendered page section 5 builds. Storing them now ships inert fields.
- **Own-storage assets stay in 2c.** This plan validates `photo_url`; it does not host it.

**Stored and served, but NOT displayed — say it out loud.** `tagline` and `how_i_work` are sanitized, drafted, reviewed, published into the Postgres row, whitelisted in `PRACTITIONER_PUBLIC_FIELDS` and returned in the `/api/p/<slug>` JSON payload. The chain is correct all the way to the payload **and then it stops**: `static/practitioner-storefront.html` renders only the practitioner name, the practice name, `bio`, the profit disclosure and the catalog link. No human being sees either new field when this branch ships. That is deliberate — a renderer cannot render a field that does not exist yet, so the field lands first and **section 5 owns the rendering**.

`photo_url`, `logo_url`, `services` and `location` are already in exactly that state, inherited from section 2a: whitelisted, published, served in the payload, drawn by nothing. This branch adds two more to that set rather than creating the condition.

Note the tension with the `seo_title` / `seo_description` deferral above, which is justified by "storing them now ships inert fields" — the same sentence is true of these two. The difference is not the shipping state, it is the ownership: `seo_*` has **no consumer at all** until section 5 builds the server-rendered page, whereas `tagline` and `how_i_work` are already live in the JSON payload, already flow through the review gate Glen operates, and are needed as stored columns before a renderer can be written against them. Both fields being invisible on merge is a known, accepted, temporary state, not an oversight — and it is pinned by `test_every_public_field_is_actually_rendered_somewhere` in `tests/test_public_surface_routes.py`, an `xfail(strict=False)` guard that turns XPASS the moment section 5 lands.

**Renderer contract for whoever writes section 5.** `how_i_work` is stored WITH its newlines (see `sanitize_how_i_work`). It must be emitted under `white-space: pre-line`, or split on blank lines into one `<p>` per paragraph. Dropping it into ordinary flowed HTML silently re-collapses the practitioner's paragraphs and bullets. Separately, every sanitizer in `dashboard/practitioner_profile.py` STRIPS markup, it does not ESCAPE it — the renderer still owns HTML-escaping.

## File Structure

- **Create `migrations/practitioner-profile-fields.sql`** — two additive columns. Applied by hand, like its siblings.
- **Modify `dashboard/practitioner_profile.py`** — two sanitizers, a URL validator, and both halves of the field set.
- **Modify `dashboard/practitioner_drafts.py`** — three `REVIEW_POLICY` entries.
- **Modify `dashboard/public_surface.py`** — two whitelist entries.
- **Modify `static/practitioner-settings.html`** — three inputs.
- **Modify `tests/test_practitioner_profile.py`** — sanitizer and validator tests.

---

### Task 1: Sanitizers for the two new fields

**Files:**
- Modify: `dashboard/practitioner_profile.py`
- Test: `tests/test_practitioner_profile.py`

**Interfaces:**
- Consumes: `_norm`, `MAX_BIO` (existing).
- Produces: `MAX_TAGLINE = 120`, `MAX_HOW_I_WORK = 2000`, `sanitize_tagline(text) -> str`, `sanitize_how_i_work(text) -> str`. Both raise `ValueError` when too long, matching `sanitize_bio`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_profile.py

def test_sanitize_tagline_strips_html_and_collapses_whitespace():
    assert pp.sanitize_tagline("  <b>Root-cause</b>   coaching ") == "Root-cause coaching"


def test_sanitize_tagline_rejects_over_the_cap():
    with pytest.raises(ValueError):
        pp.sanitize_tagline("x" * (pp.MAX_TAGLINE + 1))


def test_sanitize_tagline_accepts_exactly_the_cap():
    assert len(pp.sanitize_tagline("x" * pp.MAX_TAGLINE)) == pp.MAX_TAGLINE


def test_sanitize_how_i_work_strips_html():
    """Note the expected value: stripping <script> closes the gap between the
    words, so "start" and "x" join. That is _norm's real behaviour, verified,
    not a typo — do not "fix" it to "We start x slowly"."""
    assert pp.sanitize_how_i_work("<p>We start<script>x</script> slowly</p>") == "We startx slowly"


def test_sanitize_how_i_work_rejects_over_the_cap():
    with pytest.raises(ValueError):
        pp.sanitize_how_i_work("x" * (pp.MAX_HOW_I_WORK + 1))


def test_sanitizers_do_not_strip_the_practitioners_own_contact_detail():
    """Same rule sanitize_bio follows: over-stripping prose is a known failure
    mode, and a practitioner may legitimately name their own phone or site."""
    out = pp.sanitize_how_i_work("Call me on 808-555-0100 or see maryboyd.com")
    assert "808-555-0100" in out and "maryboyd.com" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v -k "tagline or how_i_work"`
Expected: FAIL, `AttributeError: module 'dashboard.practitioner_profile' has no attribute 'sanitize_tagline'`.

- [ ] **Step 3: Write minimal implementation**

Add next to `MAX_BIO` and `sanitize_bio` in `dashboard/practitioner_profile.py`:

```python
MAX_TAGLINE = 120
MAX_HOW_I_WORK = 2000


def sanitize_tagline(text):
    """One line under the practitioner's name. Strip HTML, collapse whitespace,
    refuse anything over MAX_TAGLINE. Raises ValueError, like sanitize_bio, so
    the settings route's existing 400 handler catches it."""
    clean = _norm(text)
    if len(clean) > MAX_TAGLINE:
        raise ValueError(f"tagline exceeds {MAX_TAGLINE} characters")
    return clean


def sanitize_how_i_work(text):
    """The longer 'how I work' prose. Same rules as sanitize_bio, bigger cap:
    the 600-char bio cannot carry a page that has to explain a practice.

    Like sanitize_bio this does NOT strip URLs, emails or phone numbers — a
    practitioner may legitimately include their own, and over-stripping prose
    is a known failure mode.
    """
    clean = _norm(text)
    if len(clean) > MAX_HOW_I_WORK:
        raise ValueError(f"how_i_work exceeds {MAX_HOW_I_WORK} characters")
    return clean
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_profile.py tests/test_practitioner_profile.py
git commit -m "feat(profile): sanitizers for tagline and how_i_work"
```

---

### Task 2: Validate photo_url and logo_url

`save_draft` currently accepts `photo_url` as `(profile.get("photo_url") or "").strip()` — no scheme check at all, so `javascript:` and `data:` URLs reach a public page.

**Files:**
- Modify: `dashboard/practitioner_profile.py`
- Test: `tests/test_practitioner_profile.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MAX_URL = 500`, `sanitize_image_url(url) -> str`. Raises `ValueError` on a disallowed scheme or over-length input. An empty input returns `""` (clearing an image is legitimate).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_profile.py

def test_image_url_accepts_https():
    assert pp.sanitize_image_url(" https://cdn.example.com/p.jpg ") == "https://cdn.example.com/p.jpg"


def test_image_url_accepts_a_site_relative_path():
    """Section 2c will serve practitioner assets from our own storage under a
    relative path; allow it now so that change needs no validator edit."""
    assert pp.sanitize_image_url("/practitioner-asset/abc123") == "/practitioner-asset/abc123"


def test_image_url_empty_is_allowed():
    assert pp.sanitize_image_url("") == ""
    assert pp.sanitize_image_url(None) == ""


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:image/svg+xml;base64,PHN2Zz4=",
    "vbscript:x",
    "http://cdn.example.com/p.jpg",     # plaintext http on an https page
    "//cdn.example.com/p.jpg",          # protocol-relative
    "ftp://x/p.jpg",
])
def test_image_url_rejects_dangerous_or_insecure(bad):
    with pytest.raises(ValueError):
        pp.sanitize_image_url(bad)


def test_image_url_rejects_over_length():
    with pytest.raises(ValueError):
        pp.sanitize_image_url("https://x/" + "a" * pp.MAX_URL)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v -k image_url`
Expected: FAIL, `AttributeError: ... has no attribute 'sanitize_image_url'`.

- [ ] **Step 3: Write minimal implementation**

```python
MAX_URL = 500


def sanitize_image_url(url):
    """An image URL safe to put on a public page.

    Allows exactly two shapes: an absolute https:// URL, or a site-relative
    path beginning with a single '/'. Everything else raises ValueError —
    `javascript:` and `data:` are script-execution vectors on a page we serve,
    plaintext `http://` is mixed content, and `//host/path` is
    protocol-relative and inherits whichever scheme the page happens to use.

    Empty input returns "" — clearing an image is legitimate.

    This validates what a PRACTITIONER submits. It deliberately does not touch
    values already in the column from scraping: those predate this rule and
    rewriting them is not this plan's business.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if len(u) > MAX_URL:
        raise ValueError(f"image URL exceeds {MAX_URL} characters")
    if u.startswith("//"):
        raise ValueError("image URL must not be protocol-relative")
    if u.startswith("/"):
        return u
    if u.lower().startswith("https://"):
        return u
    raise ValueError("image URL must be https:// or a site-relative path")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_profile.py tests/test_practitioner_profile.py
git commit -m "feat(profile): validate practitioner-supplied image URLs"
```

---

### Task 3: The migration

**Files:**
- Create: `migrations/practitioner-profile-fields.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: `practitioners.tagline` and `practitioners.how_i_work`.

- [ ] **Step 1: Write the migration**

```sql
-- migrations/practitioner-profile-fields.sql
-- Section 2b: two self-authored profile fields.
-- Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md
-- Additive + idempotent. Apply: psql "$SUPABASE_DB_URL" < migrations/practitioner-profile-fields.sql
--
-- Both are published ONLY by dashboard.practitioner_profile._write_live_profile,
-- and only from a draft Glen has approved.
--
-- DELIBERATELY does NOT re-create v_practitioners_public. That view is a stored
-- SELECT of a frozen column list feeding the PUBLIC practitioner finder, and it
-- already exposes bio, photo_url, credentials, city and state directly, with no
-- PRACTITIONER_PUBLIC_FIELDS whitelist in front of it. Adding these columns to it
-- would publish practitioner prose on a surface the storefront whitelist does not
-- guard. Same reasoning, and the same deliberate omission, as
-- migrations/practitioners-storefront.sql.
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS tagline text;
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS how_i_work text;
```

- [ ] **Step 2: Verify it is idempotent and does not touch the view**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && grep -ci "v_practitioners_public" migrations/practitioner-profile-fields.sql`
Expected: `0` matches in SQL statements — the view name appears only inside the comment block. Confirm by eye that no `CREATE OR REPLACE VIEW` exists in the file.

- [ ] **Step 3: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add migrations/practitioner-profile-fields.sql
git commit -m "feat(db): tagline and how_i_work columns, kept out of the public finder view"
```

---

### Task 4: Wire all three fields through both halves in one commit

This is the task the 2a guard test exists to police. `save_draft` and `_write_live_profile` MUST change together.

**Files:**
- Modify: `dashboard/practitioner_profile.py`
- Modify: `dashboard/practitioner_drafts.py`
- Modify: `dashboard/public_surface.py`
- Test: `tests/test_practitioner_profile.py`

**Interfaces:**
- Consumes: `sanitize_tagline`, `sanitize_how_i_work`, `sanitize_image_url` from Tasks 1-2.
- Produces: `save_draft` storing nine keys; `_write_live_profile` publishing nine columns.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_profile.py

def test_save_draft_stores_the_new_fields():
    import sqlite3
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    out = pp.save_draft(cx, "pid-1", {
        "bio": "b", "services": [], "city": "Hilo", "state": "HI",
        "photo_url": "https://x/p.jpg", "logo_url": "https://x/l.png",
        "accepting_clients": True,
        "tagline": "Root-cause coaching",
        "how_i_work": "We start slowly."})
    assert out["tagline"] == "Root-cause coaching"
    assert out["how_i_work"] == "We start slowly."
    assert out["logo_url"] == "https://x/l.png"


def test_save_draft_rejects_a_bad_image_url_before_writing():
    import sqlite3
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    with pytest.raises(ValueError):
        pp.save_draft(cx, "pid-1", {"bio": "b", "photo_url": "javascript:alert(1)"})


def test_logo_url_is_actually_published():
    """The 2a bug: logo_url is a real column, is in PRACTITIONER_PUBLIC_FIELDS,
    and was written by nothing. Publishing must include it or it stays dead."""
    import inspect
    src = inspect.getsource(pp._write_live_profile)
    assert "logo_url" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v -k "new_fields or bad_image or logo_url_is_actually"`
Expected: FAIL — `save_draft` returns no `tagline` key and `_write_live_profile`'s source has no `logo_url`.

- [ ] **Step 3: Write minimal implementation**

In `dashboard/practitioner_profile.py`, replace the `fields` dict in `save_draft`:

```python
    fields = {
        "bio": sanitize_bio(profile.get("bio", "")),
        "services": clean_services(profile.get("services")),
        "city": _norm(profile.get("city"))[:MAX_LOC_LEN],
        "state": _norm(profile.get("state"))[:MAX_LOC_LEN],
        "photo_url": sanitize_image_url(profile.get("photo_url")),
        "logo_url": sanitize_image_url(profile.get("logo_url")),
        "accepting_clients": bool(profile.get("accepting_clients", True)),
        "tagline": sanitize_tagline(profile.get("tagline", "")),
        "how_i_work": sanitize_how_i_work(profile.get("how_i_work", "")),
    }
```

And in the SAME commit, extend `_write_live_profile`'s statement:

```python
        cur.execute(
            "UPDATE practitioners SET bio=%s, photo_url=%s, logo_url=%s,"
            " specialties=%s, city=%s, state=%s, accepting_new_patients=%s,"
            " tagline=%s, how_i_work=%s,"
            " profile_self_authored_at=now(), updated_at=now() WHERE id=%s",
            (fields.get("bio", ""), fields.get("photo_url", ""),
             fields.get("logo_url", ""), fields.get("services", []),
             fields.get("city", ""), fields.get("state", ""),
             bool(fields.get("accepting_clients", True)),
             fields.get("tagline", ""), fields.get("how_i_work", ""),
             str(pid)))
```

In `dashboard/practitioner_drafts.py`, add three `REVIEW_POLICY` entries alongside the existing ones, all `"review"`:

```python
    "tagline": "review",
    "how_i_work": "review",
    "logo_url": "review",
```

(`logo_url` may already be present from 2a — if so, leave it.)

In `dashboard/public_surface.py`, add two keys to `PRACTITIONER_PUBLIC_FIELDS`:

```python
    "tagline",
    "how_i_work",
```

- [ ] **Step 4: Run the guard tests specifically**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile.py -v`
Expected: PASS, including `test_save_draft_writes_every_field_publish_reads` and `test_only_one_function_stamps_profile_self_authored_at`.

- [ ] **Step 5: Prove the coupling guard still bites**

Temporarily remove `"tagline": sanitize_tagline(...)` from `save_draft` while leaving `tagline=%s` in the publish statement. Re-run `test_save_draft_writes_every_field_publish_reads` and confirm it goes RED naming `tagline`. Restore and confirm GREEN. Both raw transcripts in your report. This is the test that stops a future field being added to one side only — confirm it still works now that the field set has grown.

- [ ] **Step 6: Run the full affected set**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_drafts.py tests/test_practitioner_profile.py tests/test_practitioner_profile_routes.py tests/test_practitioner_review_console.py tests/test_public_surface.py tests/test_public_surface_routes.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_profile.py dashboard/practitioner_drafts.py dashboard/public_surface.py tests/test_practitioner_profile.py
git commit -m "feat(profile): publish tagline, how_i_work and the previously dead logo_url"
```

---

### Task 5: The settings-page inputs

**Files:**
- Modify: `static/practitioner-settings.html`
- Test: `tests/test_practitioner_profile_routes.py`

**Interfaces:**
- Consumes: the fields from Task 4.
- Produces: nothing consumed later.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_profile_routes.py
import pathlib


def test_settings_page_offers_the_new_profile_inputs():
    """A field the practitioner cannot type is a field that does not exist.
    Section 2a shipped a submit route with no caller; this is the same check
    one layer up."""
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    for ident in ("sf-tagline", "sf-how-i-work", "sf-logo-url"):
        assert ident in html, f"settings page has no input for {ident}"


def test_settings_page_sends_the_new_fields():
    html = pathlib.Path(appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    for key in ("tagline", "how_i_work", "logo_url"):
        assert key in html, f"settings page never sends {key} in its payload"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile_routes.py -v -k settings_page`
Expected: FAIL on the missing `sf-tagline` identifier.

- [ ] **Step 3: Write minimal implementation**

In `static/practitioner-settings.html`, inside the existing "Your public storefront" panel and matching the surrounding markup style, add three inputs above the bio field:

```html
      <label class="lbl" for="sf-tagline">Tagline</label>
      <input type="text" id="sf-tagline" maxlength="120"
             placeholder="One line under your name">
      <label class="lbl" for="sf-how-i-work">How I work</label>
      <textarea id="sf-how-i-work" rows="6" maxlength="2000"
                placeholder="What working with you is actually like"></textarea>
      <label class="lbl" for="sf-logo-url">Logo URL</label>
      <input type="text" id="sf-logo-url" placeholder="https://...">
```

Then extend the loader (which pre-fills from `data.profile`) and the save payload builder to carry `tagline`, `how_i_work` and `logo_url` alongside the existing `bio`/`photo_url`/`city`/`state` handling. Follow the exact pattern the existing fields use — read them with the same accessor, mark `sfDirty` on the same events, and include them in the same `payload.profile` object.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_profile_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Syntax-check the page's JavaScript**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 - <<'EOF'
import re, pathlib, subprocess, tempfile, os
html = pathlib.Path("static/practitioner-settings.html").read_text(encoding="utf-8")
js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
p = os.path.join(tempfile.mkdtemp(), "s.js")
open(p, "w").write(js)
print(subprocess.run(["node", "--check", p], capture_output=True, text=True))
EOF`
Expected: returncode 0. There is no automated browser coverage for this page, so a syntax error would otherwise ship silently.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add static/practitioner-settings.html tests/test_practitioner_profile_routes.py
git commit -m "feat(settings): inputs for tagline, how I work, and logo"
```

---

## Done criteria

- A practitioner can type a tagline, a "how I work" paragraph, and a logo URL, and all three travel through the draft and review gate like every other field.
- `logo_url` is no longer dead: it is written by `save_draft` and published by `_write_live_profile`.
- A `javascript:` or `data:` image URL is refused at the point the practitioner submits it.
- The coupling guard still bites — proven, not assumed — now that the field set has grown to nine.
- `v_practitioners_public` is unchanged, so the new prose reaches only the whitelisted storefront.

## Deploy runbook

Five steps, in this order. The order is not cosmetic — getting step 2 wrong fails **silently**, not loudly.

**1. Verify the section-2a columns already exist in prod.**

`practitioners.logo_url` and `practitioners.profile_self_authored_at` come from `migrations/practitioners-storefront.sql`. That file is on `main`, but like every migration here it is **applied by hand** — being merged is not evidence it ran. Verify, do not assume:

```sql
SELECT column_name FROM information_schema.columns
 WHERE table_name = 'practitioners'
   AND column_name IN ('logo_url', 'profile_self_authored_at', 'tagline', 'how_i_work');
```

Expect `logo_url` and `profile_self_authored_at` before you start; `tagline` and `how_i_work` appear after step 2.

**2. Apply `migrations/practitioner-profile-fields.sql` to prod Postgres — BEFORE the code merges.**

```
psql "$SUPABASE_DB_URL" < migrations/practitioner-profile-fields.sql
```

Additive and idempotent (`ADD COLUMN IF NOT EXISTS`), so running it early or twice is safe. **Running it late is not.** The failure mode if the code ships first is silent degradation, not an error page:

- `profile_for_slug` now selects `tagline, how_i_work`. Against a table without those columns, Postgres raises `UndefinedColumn` — and that function's `except Exception: return {}` catches it and returns an empty profile, with **no log line at all**. Every self-authored practitioner page quietly drops to name + profit disclosure. The page still renders 200. Nothing alerts.
- The settings GET selects the same two columns. Its `except Exception` does print, but from the practitioner's side the symptom is her editor loading **blank** — bio, photo, services, city, state, tagline, how_i_work all empty — which reads as "my profile was deleted", and her natural next move is to retype and save.
- Publishing (`_write_live_profile`) writes both columns unconditionally, so an approve would raise there too.

So: migration first, and confirm with the query in step 1 before merging anything.

**3. Merge section 2a — `sess/a191e6ab-s2`, PR #1481.**

2b is stacked on it and **2a is not an ancestor of `main`** (verified: `git merge-base --is-ancestor origin/sess/a191e6ab-s2 origin/main` fails; 13 commits are unique to 2a). The draft store, the review gate and the guard tests all come from 2a. **2b cannot merge alone** — merging it without 2a first gives a branch whose imports and tests reference code that is not there.

**4. Merge 2b — `sess/a191e6ab-s2b`.**

**5. Set `PRACTITIONER_REVIEW_GATE_ENABLED` in Doppler `prd`.**

The flag defaults **OFF** (`app.py`, `_practitioner_review_gate_enabled`). With it off, a practitioner's save publishes to her live page **immediately, with no review** — that is 2a's deliberate pre-feature behaviour, kept so the merge itself changes nothing anyone can see. "The beta reviews every field before it goes public" is only true once the flag is on.

**Merge plus flag flip is TWO deploys.** Merging step 4 does not turn the gate on, and setting the Doppler variable is a separate deploy of its own. Budget for both, and tell any practitioner who is mid-edit before flipping it: from that moment her next save queues for Glen instead of going live.

### Not part of the deploy, but true on the day

`tagline` and `how_i_work` will be stored, published, whitelisted and served in `/api/p/<slug>` — and **displayed nowhere**, because section 5 owns the storefront rendering. See the Scope boundary above. Do not treat a practitioner reporting "I saved my tagline and my page didn't change" as a deploy failure; it is the expected state until section 5 ships.
