# Practitioner Page Slug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate a practitioner's public identity (the URL people see) from her affiliate attribution code, so Glen's page and booking live at `/dr-glen` and `/book/dr-glen` while `remedy-match` keeps working as his affiliate slug forever.

**Architecture:** Add a nullable `page_slug` column to `affiliate_signups`. NULL means "use `slug`", so every existing practitioner is unaffected. One resolver maps a request slug to (kind, canonical_slug, affiliate_slug) and is used by BOTH the public page route and the booking routes, which today resolve slugs through two unrelated code paths. One helper computes a practitioner's canonical URL and is used by every site that prints one.

**Tech Stack:** Flask, `dashboard/practitioner_slugs.py`, `dashboard/practitioner_booking.py`, `dashboard/public_surface.py`, `app.py`, `static/practitioner-booking.html`. Dual store: SQLite `LOG_DB` and Postgres, reached through `dashboard/db.py` (which translates `?` placeholders). **Production runs `DB_BACKEND=postgres`.**

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md` (vanity URLs)

## Global Constraints

- **`affiliate_signups.slug` is never written by this feature.** It is the attribution key: stored lead rows carry `utm_source=<slug>`, the Rebrandly shortlink `truly.vip/<slug>`, `?ref=` cookies with 90-day last-touch, and the cert/recruit/dispensary URLs. Renaming it orphans attribution.
- **The legacy URL keeps working forever.** No expiry on the old-slug redirect.
- **Redirect legacy → canonical with 302, not 301.** `page_slug` is practitioner-changeable; a 301 is cached indefinitely by browsers, so a later rename would strand everyone who visited under the previous name. The existing 301 for case normalization (`/Mary-Boyd` → `/mary-boyd`) stays 301: it is deterministic and can never change.
- **Analytics stay keyed on the affiliate slug**, never the display slug, so changing a vanity URL does not split a practitioner's view history. `public_surface.record_view` is the call site.
- **A page_slug must be unique across the whole namespace** — against every row's `slug` AND every row's `page_slug`, at any status, for the reason `slug_is_taken`'s docstring already gives: a pending practitioner's approval must never shadow a published URL.
- **Reserved words are checked against the live url_map**, via the existing `practitioner_slugs.reserved_for(url_map)`, not a hand-written list.
- Identity always comes from `_practitioner_session_pid()`. A practitioner_id in a request body is ignored.
- The renderer stays pure: reads no DB, no env; every input arrives as an argument.
- Copy rules: no em dashes, no `--` in practitioner-facing strings, "client" not "patient".

---

### Task 1: `page_slug` storage, validation and resolution

**Files:**
- Modify: `dashboard/practitioner_slugs.py`
- Test: `tests/test_practitioner_slugs.py`

**Interfaces:**
- Consumes: existing `normalize`, `check_shape`, `check_not_reserved`, `reserved_for`, `SlugError`, `canonical_exists`.
- Produces:
  - `init_page_slug(cx)` — additive `ALTER TABLE affiliate_signups ADD COLUMN page_slug TEXT`, wrapped in the same `try/except Exception: pass` idiom `practitioner_booking.init_tables` uses, plus a `CREATE UNIQUE INDEX IF NOT EXISTS ux_affiliate_page_slug ON affiliate_signups(page_slug)`.
  - `canonical_slug_for(cx, affiliate_slug) -> str` — the row's `page_slug` or its `slug`.
  - `page_slug_is_taken(cx, candidate, *, excluding_affiliate_slug=None) -> bool` — True if any row's `slug` or `page_slug` equals `candidate`, ignoring the claimant's own row.
  - `validate_page_slug(cx, candidate, *, owner_affiliate_slug, reserved) -> str` — normalizes, `check_shape`, `check_not_reserved`, then `page_slug_is_taken`; raises `SlugError` with a practitioner-readable message.
  - `set_page_slug(cx, affiliate_slug, candidate, *, reserved) -> str` — validates then writes; passing `""`/None clears it (back to the affiliate slug).
  - `resolve_page(cx, requested) -> (kind, canonical, affiliate_slug)` where kind is `"canonical"`, `"legacy"` or `""`. Match `page_slug` first, then `slug`. When the request matched `slug` and that row has a different `page_slug`, kind is `"legacy"` and `canonical` is the `page_slug`.

- [ ] **Step 1: Write the failing tests**

Cover, in `tests/test_practitioner_slugs.py`, using the module's own DDL and writers to build the fixture (never a hand-authored `CREATE TABLE`):

```python
def test_a_row_with_no_page_slug_resolves_to_itself(cx):
    _seed(cx, slug="mary-boyd")
    assert ps.resolve_page(cx, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")
    assert ps.canonical_slug_for(cx, "mary-boyd") == "mary-boyd"

def test_the_page_slug_becomes_canonical_and_the_affiliate_slug_is_legacy(cx):
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    assert ps.resolve_page(cx, "dr-glen") == ("canonical", "dr-glen", "remedy-match")
    assert ps.resolve_page(cx, "remedy-match") == ("legacy", "dr-glen", "remedy-match")
    assert ps.canonical_slug_for(cx, "remedy-match") == "dr-glen"

def test_an_unknown_slug_resolves_to_nothing(cx):
    assert ps.resolve_page(cx, "nobody") == ("", "", "")

def test_a_page_slug_cannot_take_another_practitioners_affiliate_slug(cx):
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "mary-boyd", reserved=frozenset())

def test_a_page_slug_cannot_take_another_practitioners_page_slug(cx):
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "the-coach", reserved=frozenset())

def test_reclaiming_your_own_page_slug_is_not_a_collision(cx):
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    assert ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset()) == "dr-glen"

def test_a_reserved_word_is_refused(cx):
    _seed(cx, slug="remedy-match")
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "book", reserved=frozenset({"book"}))

def test_clearing_the_page_slug_restores_the_affiliate_slug(cx):
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    ps.set_page_slug(cx, "remedy-match", "", reserved=frozenset())
    assert ps.resolve_page(cx, "remedy-match") == ("canonical", "remedy-match", "remedy-match")
    assert ps.resolve_page(cx, "dr-glen") == ("", "", "")

def test_init_page_slug_is_idempotent(cx):
    ps.init_page_slug(cx); ps.init_page_slug(cx)   # must not raise on the second call
```

- [ ] **Step 2: Run them and watch them fail** — `pytest tests/test_practitioner_slugs.py -v`. Expected: `AttributeError` on the new names.
- [ ] **Step 3: Implement the functions above in `dashboard/practitioner_slugs.py`.**
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Mutation-test the uniqueness guard.** Delete the `page_slug_is_taken` check inside `validate_page_slug`, confirm `test_a_page_slug_cannot_take_another_practitioners_affiliate_slug` goes RED, restore, confirm GREEN. Record the actual failure output in the report.
- [ ] **Step 6: Commit.**

---

### Task 2: One resolver for the page route and the booking routes

**Files:**
- Modify: `app.py` (`practitioner_site` at ~20255, the `/p/<slug>` alias, `_render_practitioner_page` at ~20099)
- Modify: `dashboard/practitioner_booking.py` (`resolve_practitioner_pid` at ~509)
- Modify: `dashboard/public_surface.py` (`build_practitioner_storefront`, `record_view` call site)
- Test: `tests/test_practitioner_site_render.py`, `tests/test_practitioner_booking_routes.py`

**Interfaces:**
- Consumes: `resolve_page`, `canonical_slug_for` from Task 1.
- Produces: booking and page routes that both accept the canonical page slug and 302 the legacy affiliate slug.

Today these are two unrelated paths: `practitioner_site` uses `practitioner_slugs.resolve`, while `practitioner_booking.resolve_practitioner_pid` reads `affiliate_signups WHERE slug=? AND status='approved'` directly and knows nothing about aliases. **This is why `/book/<alias>` 404s today.** Both must go through `resolve_page`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_public_page_serves_the_canonical_page_slug(client, cx):
    # /dr-glen -> 200 and its canonical tag says /dr-glen
def test_the_public_page_302s_the_legacy_affiliate_slug(client, cx):
    # /remedy-match -> 302 to /dr-glen. 302 NOT 301: assert the exact status code.
def test_case_normalisation_stays_a_301(client, cx):
    # /Dr-Glen -> 301 /dr-glen  (deterministic, unlike a changeable page_slug)
def test_booking_resolves_the_canonical_page_slug(client, cx):
    # /api/book/dr-glen/slots -> 200 with her real session types
def test_booking_302s_the_legacy_affiliate_slug(client, cx):
    # /book/remedy-match -> 302 /book/dr-glen
def test_a_view_is_recorded_under_the_affiliate_slug_not_the_page_slug(cx):
    # record_view called with "remedy-match" so history does not split
```

- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** `resolve_practitioner_pid` keeps its signature and fail-closed contract (a missing table or broken read means "no such practitioner", never an exception on a public page) — only its lookup changes. `_render_practitioner_page`'s `canonical_slug` argument must be the page slug, so the canonical tag and JSON-LD `url` agree with the address bar.
- [ ] **Step 4: Run the tests to green.**
- [ ] **Step 5: Mutation-test the legacy redirect.** Make the legacy branch serve 200 instead of redirecting; confirm `test_the_public_page_302s_the_legacy_affiliate_slug` goes RED; restore. Then separately change the 302 to a 301 and confirm the same test goes RED on the status code.
- [ ] **Step 6: Run `tests/test_slug_route_collision.py`** — the existing guard that stops a new route stealing a live practitioner's URL. It must stay green.
- [ ] **Step 7: Commit.**

---

### Task 3: The endpoint and the settings UI, including her own URLs

**Files:**
- Modify: `app.py` (new `POST /api/practitioner/page-slug`, beside `/api/practitioner/booking-config`)
- Modify: `static/practitioner-booking.html`
- Test: `tests/test_practitioner_booking_routes.py`

This also closes a live gap: **nothing in the practitioner UI ever tells her her own public URL or booking URL.** She configures booking, sees "Saved", and has no way to learn where her page is. Both URLs must be displayed, and both must be selectable so she can copy them.

**Interfaces:**
- Consumes: `set_page_slug`, `canonical_slug_for`, `reserved_for` from Task 1.
- Produces: `POST /api/practitioner/page-slug` taking `{"page_slug": "..."}`; the booking-config GET additionally returns `page_slug`, `canonical_slug`, `page_url`, `booking_url`.

- [ ] **Step 1: Write the failing tests**

```python
def test_page_slug_post_requires_a_session(client):
    # 401, no write
def test_page_slug_post_ignores_a_practitioner_id_in_the_body(client, ...):
    # claims for the SESSION's practitioner, never the body's
def test_page_slug_post_refuses_a_reserved_word(client, ...):
    # 400 with a readable message, nothing written
def test_page_slug_post_refuses_another_practitioners_slug(client, ...):
    # 400, nothing written
def test_the_booking_config_get_returns_both_urls(client, ...):
    # page_url and booking_url present and built from the CANONICAL slug
```

- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement the route.** pid from `_practitioner_session_pid()` only. Map the pid to its affiliate row, refuse with a clear message if the practitioner has no affiliate row at all (nothing to attach a page slug to). `SlugError` becomes a 400 whose body is the message; the form shows it inline.
- [ ] **Step 4: Implement the UI.** A "Your web address" section showing the live page URL and booking URL, and a field to change the vanity slug with the error rendered inline. Reuse the page's existing `loaded` lockout: a form that failed to load must not be saveable.
- [ ] **Step 5: Run the tests to green.**
- [ ] **Step 6: Mutation-test the ownership guard.** Make the route honour `body["practitioner_id"]`; confirm `test_page_slug_post_ignores_a_practitioner_id_in_the_body` goes RED; restore.
- [ ] **Step 7: Commit.**

---

### Task 4: Audit existing slugs, then set Glen's

**Files:**
- Create: `scripts/audit_page_slugs.py`
- Test: `tests/test_practitioner_slugs.py` (one addition)

A new uniqueness rule owes an audit of the identifiers that already exist: if two rows already collide under the new rule, the constraint cannot be created and the feature fails on deploy rather than on a save.

- [ ] **Step 1: Write the audit script.** Read-only. Report any existing `affiliate_signups.slug` that collides with another row's slug under `normalize`, and any slug that is a reserved word under `reserved_for(app.url_map)`. Print a count and the offenders; exit non-zero if any.
- [ ] **Step 2: Add a test** that the unique index creation is safe when two rows have NULL `page_slug` — NULLs must not collide with each other (verify on BOTH backends' semantics; Postgres and SQLite both allow multiple NULLs in a UNIQUE index, and the test pins that).
- [ ] **Step 3: Run the audit against a fixture DB.** Report the result.
- [ ] **Step 4: Commit.**

---

### Task 5: Verify on Postgres

**Files:** none (verification only)

Prod runs `DB_BACKEND=postgres`; the suite runs on SQLite. The additive `ALTER` plus a `CREATE UNIQUE INDEX` is exactly the shape that behaves differently there: on Postgres a failed statement aborts the transaction, so a swallowed `DuplicateColumn` can poison every later statement on that connection.

- [ ] **Step 1: Start a local Postgres** (`initdb` + `pg_ctl` on a spare port, socket dir under `/tmp` — the socket path must stay under 103 bytes).
- [ ] **Step 2: Exercise, with `DB_BACKEND=postgres` and `PG_DSN` set:** `init_page_slug` twice on the same connection, then a read in the SAME transaction (this is the aborted-transaction trap); `set_page_slug`; `resolve_page` for canonical, legacy and unknown; a duplicate claim that must raise; and `canonical_slug_for`.
- [ ] **Step 3: Report** the actual output of each, and tear the instance down.

---

### Task 6: Set Glen's page slug in production

**Files:** none (operational)

- [ ] **Step 1: After deploy,** POST `/api/practitioner/page-slug` with `{"page_slug": "dr-glen"}` using a practitioner session for `drglenswartwout@gmail.com`.
- [ ] **Step 2: Verify on the live site:** `/dr-glen` serves 200 with a canonical tag of `/dr-glen`; `/remedy-match` 302s to it; `/book/dr-glen` serves the booking page; `/book/remedy-match` 302s to it; `/api/book/dr-glen/slots` returns his two session types.
- [ ] **Step 3: Verify attribution is untouched:** `?ref=remedy-match` still sets the referral cookie, and the affiliate dashboard still reports `slug: remedy-match` with its tracking URL unchanged.
