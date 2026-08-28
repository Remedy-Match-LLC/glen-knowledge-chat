# Practitioner Site URLs and Slug Governance - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give practitioners a stable, clean, collision-proof URL at `myhealingoasis.com/<slug>`, with one canonical slug that serves content and optional alternates that redirect to it.

**Architecture:** A new `dashboard/practitioner_slugs.py` owns the slug namespace: shape validation, reserved-word derivation from the live Flask URL map, an alias table, and claim/lookup. `app.py` gains a host-gated catch-all `/<slug>` route registered last, which serves the existing storefront page unchanged. A CI guard walks `app.url_map` and fails if any route rule collides with a published slug.

**Tech Stack:** Python 3, Flask/Werkzeug, sqlite (LOG_DB) via `dashboard.db`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-practitioner-website-design.md` (section 1)

## Global Constraints

- **Host gate:** practitioner sites serve only on the `PORTAL_BASE_URL` host (`https://myhealingoasis.com`). Use the existing `app._on_portal_host()` (`app.py:392`). `illtowell.com` behavior is unchanged.
- **Flag gate:** every new public route also respects `app._public_surface_enabled()`, matching `/p/<slug>`.
- **`noindex` stays on in this plan.** Lifting it is section 5 of the spec, not this work. Every response here keeps `X-Robots-Tag: noindex`.
- **Canonical serves, alternates redirect.** Alternates 301 to the canonical, never render content, never enter a sitemap.
- **Canonical slug is immutable after publish.** No task here changes an existing `affiliate_signups.slug`.
- **Mary's canonical slug is `mary-boyd`**, produced by the existing `_mint_affiliate_slug`. This plan does not change minting.
- **Tests:** module-level `pytest.skip` when `PINECONE_API_KEY` is absent, then `import app as appmod` and `monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))`. **Never `importlib.reload(app)`** - it leaks schedulers and makes CI flaky.
- **Git:** stage named files only. **Never `git add -A` or `git add .`** in this checkout; another session's work can be swept in.
- **Working tree:** worktree `/tmp/wt-deploy-chat-a191e6ab`, branch `sess/a191e6ab`.

## Scope boundary

**In scope:** the slug namespace, the canonical route, alternate redirects, the collision guard, and the stale URL text in the practitioner settings page.

**Out of scope, deliberately:** the self-serve alternate-claim UI and its approval queue. Section 2 of the spec builds the draft-and-review mechanism, and the claim form belongs in that queue rather than in a second one built here. The two human checks the spec attaches to a claim - Glen's review for profanity and impersonation, and credential verification for any alternate carrying a title such as `-rn` - are part of that deferred flow, not of this plan.

Until then, `claim_alias()` is called by Glen from the console or a shell, which is sufficient for a single beta tenant. **The real call must pass the live reserved set**, not an empty one:

```python
from dashboard import db, practitioner_slugs as ps
import app as appmod
with db.connect(appmod.LOG_DB) as cx:
    ps.claim_alias(cx, "mary-boyd", "boyd-coaching",
                   ps.reserved_for(appmod.app.url_map))
```

The unit tests in Task 3 pass `frozenset()` deliberately, to isolate the reserved-word check from the shape and uniqueness checks. A production caller that copied that would let a practitioner claim `login`.

## File Structure

- **Create `dashboard/practitioner_slugs.py`** - the whole slug namespace. Pure validation helpers, reserved-segment derivation, the alias table, and claim/lookup. No Flask imports, so it is unit-testable without importing `app`.
- **Modify `app.py`** - add two routes near the existing `/p/<slug>` handler (`app.py:18609`), and register the catch-all last.
- **Modify `static/practitioner-settings.html:153`** - the storefront URL text, currently wrong.
- **Create `tests/test_practitioner_slugs.py`** - unit tests for the module, no `app` import.
- **Create `tests/test_practitioner_site_routes.py`** - route tests through the Flask test client.
- **Create `tests/test_slug_route_collision.py`** - the CI guard.

---

### Task 1: Slug shape validation and normalization

**Files:**
- Create: `dashboard/practitioner_slugs.py`
- Test: `tests/test_practitioner_slugs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize(raw: str) -> str`, `MIN_LEN: int`, `MAX_LEN: int`, `SlugError(ValueError)`, `check_shape(slug: str) -> None` (raises `SlugError`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_practitioner_slugs.py
"""Unit tests for the practitioner slug namespace. Imports no Flask app."""
import pytest

from dashboard import practitioner_slugs as ps


def test_normalize_lowercases_and_strips():
    assert ps.normalize("  Mary-Boyd  ") == "mary-boyd"


def test_normalize_handles_none_and_empty():
    assert ps.normalize(None) == ""
    assert ps.normalize("") == ""


@pytest.mark.parametrize("good", ["mary-boyd", "abc", "a1-b2-c3", "healing-oasis-hilo"])
def test_check_shape_accepts_valid(good):
    ps.check_shape(good)  # must not raise


@pytest.mark.parametrize("bad", [
    "-mary",          # leading hyphen
    "mary-",          # trailing hyphen
    "mary--boyd",     # doubled hyphen
    "Mary-Boyd",      # uppercase
    "mary boyd",      # space
    "mary_boyd",      # underscore
    "mary.boyd",      # dot
    "ab",             # too short
    "a" * 41,         # too long
    "",               # empty
])
def test_check_shape_rejects_invalid(bad):
    with pytest.raises(ps.SlugError):
        ps.check_shape(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `practitioner_slugs`.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/practitioner_slugs.py
"""Practitioner site slug namespace: one canonical slug, zero or more alternates.

Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md section 1.

A practitioner has exactly one CANONICAL slug (affiliate_signups.slug, minted by
dashboard.affiliate_dashboard._mint_affiliate_slug) and zero or more ALTERNATES.
The canonical serves content. Alternates 301 to it and never render, so they
cannot compete with the canonical as duplicate content.

Both kinds share ONE namespace: an alternate may collide with neither another
alternate, nor any canonical, nor any reserved route segment.

Imports no Flask app, so it is unit-testable on its own.
"""

import re

MIN_LEN = 3
MAX_LEN = 40

# Rejects leading, trailing, and doubled hyphens by construction.
_SHAPE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SlugError(ValueError):
    """A proposed slug is malformed, reserved, or already taken."""


def normalize(raw):
    """Lowercase and strip. Does NOT rewrite an invalid slug into a valid one:
    normalizing away a bad character would silently hand back a slug the
    practitioner did not ask for."""
    return (raw or "").strip().lower()


def check_shape(slug):
    """Raise SlugError unless `slug` is 3-40 chars of lowercase alphanumerics
    separated by single internal hyphens."""
    if not slug:
        raise SlugError("slug is empty")
    if len(slug) < MIN_LEN:
        raise SlugError(f"slug must be at least {MIN_LEN} characters")
    if len(slug) > MAX_LEN:
        raise SlugError(f"slug must be at most {MAX_LEN} characters")
    if not _SHAPE_RE.match(slug):
        raise SlugError(
            "slug must be lowercase letters, digits, and single internal hyphens")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_slugs.py tests/test_practitioner_slugs.py
git commit -m "feat(slugs): shape validation and normalization for practitioner slugs"
```

---

### Task 2: Reserved segments derived from the live URL map

Deriving the blocklist from `app.url_map` instead of hardcoding it means the list can never go stale as routes are added. The static extras cover words we may want as routes later but do not own yet.

**Files:**
- Modify: `dashboard/practitioner_slugs.py`
- Test: `tests/test_practitioner_slugs.py`

**Interfaces:**
- Consumes: `check_shape`, `SlugError` from Task 1.
- Produces: `EXTRA_RESERVED: frozenset[str]`, `route_segments(url_map) -> frozenset[str]`, `reserved_for(url_map) -> frozenset[str]`, `check_not_reserved(slug: str, reserved: frozenset) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_slugs.py
from werkzeug.routing import Map, Rule


def _fake_map():
    return Map([
        Rule("/"),
        Rule("/portal"),
        Rule("/api/p/<slug>"),
        Rule("/begin/explore"),
        Rule("/<slug>"),          # our own catch-all must NOT reserve itself
    ])


def test_route_segments_extracts_static_first_segments():
    segs = ps.route_segments(_fake_map())
    assert "portal" in segs
    assert "api" in segs
    assert "begin" in segs


def test_route_segments_ignores_dynamic_and_root():
    segs = ps.route_segments(_fake_map())
    assert "<slug>" not in segs
    assert "" not in segs
    # The catch-all itself contributes nothing, or no slug could ever be valid.
    assert segs == {"portal", "api", "begin"}


def test_reserved_for_unions_route_segments_and_extras():
    reserved = ps.reserved_for(_fake_map())
    assert "portal" in reserved          # from the map
    assert "login" in reserved           # from EXTRA_RESERVED
    assert "www" in reserved


def test_check_not_reserved_rejects_a_reserved_word():
    with pytest.raises(ps.SlugError):
        ps.check_not_reserved("portal", ps.reserved_for(_fake_map()))


def test_check_not_reserved_accepts_a_free_word():
    ps.check_not_reserved("mary-boyd", ps.reserved_for(_fake_map()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v -k "reserved or segments"`
Expected: FAIL with `AttributeError: module 'dashboard.practitioner_slugs' has no attribute 'route_segments'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to dashboard/practitioner_slugs.py

# Words we do not route today but may want to. A slug claimed here would have to
# be broken later, and breaking a published URL is the one thing this design
# promises never to do.
EXTRA_RESERVED = frozenset({
    "about", "account", "accounts", "app", "apps", "auth", "billing", "blog",
    "book", "booking", "cart", "checkout", "contact", "docs", "faq", "help",
    "home", "index", "info", "login", "logout", "mail", "media", "news", "pages",
    "press", "pricing", "profile", "profiles", "register", "root", "search",
    "settings", "shop", "signin", "signup", "site", "sites", "store", "support",
    "team", "test", "user", "users", "www",
})


def route_segments(url_map):
    """The set of STATIC first path segments in a Werkzeug Map.

    Dynamic segments are skipped, so the practitioner catch-all `/<slug>` does
    not reserve itself into oblivion. The root rule contributes nothing.
    """
    out = set()
    for rule in url_map.iter_rules():
        parts = (rule.rule or "").split("/")
        if len(parts) < 2:
            continue
        first = parts[1]
        if not first or "<" in first:
            continue
        out.add(first.lower())
    return frozenset(out)


def reserved_for(url_map):
    """Every word a practitioner slug may not be: live route segments plus the
    static buffer of words we may want to route later."""
    return frozenset(route_segments(url_map) | EXTRA_RESERVED)


def check_not_reserved(slug, reserved):
    """Raise SlugError if `slug` is a reserved word."""
    if slug in reserved:
        raise SlugError(f"'{slug}' is reserved")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v`
Expected: PASS, all tests including the 5 new ones.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_slugs.py tests/test_practitioner_slugs.py
git commit -m "feat(slugs): derive reserved words from the live URL map plus a buffer list"
```

---

### Task 3: The alias table, claim, and lookup

**Files:**
- Modify: `dashboard/practitioner_slugs.py`
- Test: `tests/test_practitioner_slugs.py`

**Interfaces:**
- Consumes: `normalize`, `check_shape`, `check_not_reserved`, `SlugError` from Tasks 1-2.
- Produces: `init_tables(cx) -> None`, `canonical_exists(cx, slug) -> bool`, `alias_owner(cx, alias) -> str`, `resolve(cx, slug) -> tuple[str, str]`, `claim_alias(cx, canonical, alias, reserved) -> None`.

`resolve` returns `(kind, canonical_slug)` where `kind` is `"canonical"`, `"alias"`, or `""` when unknown.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_slugs.py
import sqlite3


def _cx(tmp_path):
    """A sqlite connection seeded with the real affiliate_signups columns this
    module reads. Only the columns under test are declared; the module must not
    depend on any others."""
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    cx.execute("CREATE TABLE affiliate_signups ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,"
               " slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'approved')")
    cx.execute("INSERT INTO affiliate_signups (name,email,slug,status)"
               " VALUES ('Mary Boyd','m@x.com','mary-boyd','approved')")
    cx.execute("INSERT INTO affiliate_signups (name,email,slug,status)"
               " VALUES ('Pending Pat','p@x.com','pending-pat','pending')")
    cx.commit()
    ps.init_tables(cx)
    return cx


def test_canonical_exists_only_for_approved(tmp_path):
    cx = _cx(tmp_path)
    assert ps.canonical_exists(cx, "mary-boyd") is True
    assert ps.canonical_exists(cx, "pending-pat") is False
    assert ps.canonical_exists(cx, "nobody") is False


def test_resolve_canonical(tmp_path):
    assert ps.resolve(_cx(tmp_path), "mary-boyd") == ("canonical", "mary-boyd")


def test_resolve_unknown(tmp_path):
    assert ps.resolve(_cx(tmp_path), "nobody") == ("", "")


def test_claim_alias_then_resolve(tmp_path):
    cx = _cx(tmp_path)
    ps.claim_alias(cx, "mary-boyd", "healing-oasis-hilo", frozenset())
    assert ps.alias_owner(cx, "healing-oasis-hilo") == "mary-boyd"
    assert ps.resolve(cx, "healing-oasis-hilo") == ("alias", "mary-boyd")


def test_claim_alias_rejects_reserved_word(tmp_path):
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "mary-boyd", "portal", frozenset({"portal"}))


def test_claim_alias_rejects_bad_shape(tmp_path):
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "mary-boyd", "Bad--Shape", frozenset())


def test_claim_alias_rejects_another_practitioners_canonical(tmp_path):
    """One namespace: an alias may not shadow anyone's canonical slug."""
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "mary-boyd", "pending-pat", frozenset())


def test_claim_alias_rejects_a_duplicate_alias(tmp_path):
    cx = _cx(tmp_path)
    ps.claim_alias(cx, "mary-boyd", "healing-oasis-hilo", frozenset())
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "mary-boyd", "healing-oasis-hilo", frozenset())


def test_claim_alias_is_idempotent_only_by_raising_not_by_overwriting(tmp_path):
    """A second claim must not silently re-home an alias to a new canonical."""
    cx = _cx(tmp_path)
    ps.claim_alias(cx, "mary-boyd", "shared-name", frozenset())
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "pending-pat", "shared-name", frozenset())
    assert ps.alias_owner(cx, "shared-name") == "mary-boyd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v -k "alias or resolve or canonical"`
Expected: FAIL with `AttributeError: module 'dashboard.practitioner_slugs' has no attribute 'init_tables'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to dashboard/practitioner_slugs.py
import datetime


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_tables(cx):
    """Create the alias table. Idempotent; safe to call on every read path,
    matching dashboard.referrals.init_tables."""
    cx.execute("CREATE TABLE IF NOT EXISTS practitioner_slug_aliases ("
               "alias TEXT PRIMARY KEY, canonical_slug TEXT NOT NULL,"
               " created_at TEXT NOT NULL)")
    cx.commit()


def canonical_exists(cx, slug):
    """True iff `slug` is an APPROVED practitioner's canonical slug."""
    row = cx.execute(
        "SELECT 1 FROM affiliate_signups WHERE slug=? AND status='approved'",
        (normalize(slug),)).fetchone()
    return row is not None


def alias_owner(cx, alias):
    """The canonical slug an alias points at, or '' when the alias is unknown."""
    init_tables(cx)
    row = cx.execute(
        "SELECT canonical_slug FROM practitioner_slug_aliases WHERE alias=?",
        (normalize(alias),)).fetchone()
    return (row[0] or "") if row else ""


def resolve(cx, slug):
    """Resolve a URL slug to ('canonical'|'alias'|'', canonical_slug).

    Canonical is checked FIRST. A canonical slug can never be shadowed by an
    alias, because claim_alias refuses to create one that collides.
    """
    s = normalize(slug)
    if canonical_exists(cx, s):
        return ("canonical", s)
    owner = alias_owner(cx, s)
    if owner and canonical_exists(cx, owner):
        return ("alias", owner)
    return ("", "")


def claim_alias(cx, canonical, alias, reserved):
    """Reserve `alias` as a redirect to `canonical`. Raises SlugError if the
    alias is malformed, reserved, already an alias, or anyone's canonical.

    Fails closed: every check runs before the insert, and the alias PRIMARY KEY
    is the backstop against a concurrent duplicate.
    """
    init_tables(cx)
    a = normalize(alias)
    c = normalize(canonical)
    check_shape(a)
    check_not_reserved(a, reserved)
    if canonical_exists(cx, a):
        raise SlugError(f"'{a}' is already a practitioner's canonical slug")
    if alias_owner(cx, a):
        raise SlugError(f"'{a}' is already claimed as an alias")
    try:
        cx.execute("INSERT INTO practitioner_slug_aliases"
                   " (alias, canonical_slug, created_at) VALUES (?,?,?)",
                   (a, c, _now()))
        cx.commit()
    except db.IntegrityError as e:           # concurrent claim won the race
        raise SlugError(f"'{a}' is already claimed as an alias") from e
```

Add `from dashboard import db` to the module's import block alongside `import re`.

**Use `db.IntegrityError`, not `sqlite3.IntegrityError`.** `dashboard/db.py:18` defines it as a tuple of `(sqlite3.IntegrityError, psycopg.IntegrityError)` when psycopg is installed. Catching the bare sqlite class would silently fail to catch a unique violation on Postgres, turning a handled duplicate claim into a 500. The tuple also matches the raw `sqlite3` connections the unit tests use, so both paths are covered. This is the same pattern as `dashboard/referrals.py:44`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && python3 -m pytest tests/test_practitioner_slugs.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add dashboard/practitioner_slugs.py tests/test_practitioner_slugs.py
git commit -m "feat(slugs): alias table with one-namespace claim and resolution"
```

---

### Task 4: The route-collision guard

This is the guard the spec requires. It must be proven to bite before it is trusted.

**Files:**
- Create: `tests/test_slug_route_collision.py`

**Interfaces:**
- Consumes: `route_segments` from Task 2.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slug_route_collision.py
"""Guard: no registered route may shadow a published practitioner slug.

A root-level catch-all shares its namespace with the whole application. Without
this test, adding a route named after a live practitioner silently takes their
URL away, and we would hear about it from the practitioner rather than from CI.
"""
import os
import sqlite3

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import practitioner_slugs as ps


def _seed(db_path, slugs, aliases=()):
    cx = sqlite3.connect(db_path)
    cx.execute("CREATE TABLE IF NOT EXISTS affiliate_signups ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,"
               " slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'approved')")
    for i, s in enumerate(slugs):
        cx.execute("INSERT INTO affiliate_signups (name,email,slug,status)"
                   " VALUES (?,?,?,'approved')", (s, f"{i}@x.com", s))
    ps.init_tables(cx)
    for a, c in aliases:
        cx.execute("INSERT INTO practitioner_slug_aliases"
                   " (alias,canonical_slug,created_at) VALUES (?,?,'2026-08-27')",
                   (a, c))
    cx.commit()
    cx.close()


def _published_slugs(db_path):
    cx = sqlite3.connect(db_path)
    ps.init_tables(cx)
    canonical = {r[0] for r in cx.execute(
        "SELECT slug FROM affiliate_signups WHERE status='approved'")}
    alias = {r[0] for r in cx.execute(
        "SELECT alias FROM practitioner_slug_aliases")}
    cx.close()
    return canonical | alias


def test_no_route_shadows_a_published_slug(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db, ["mary-boyd"], aliases=[("healing-oasis-hilo", "mary-boyd")])
    reserved = ps.route_segments(appmod.app.url_map)
    collisions = sorted(_published_slugs(db) & reserved)
    assert collisions == [], (
        f"These published practitioner slugs are shadowed by routes: {collisions}. "
        "Either rename the route or migrate the practitioner and 301 the old slug.")


def test_guard_detects_a_planted_collision(tmp_path):
    """Mutation test: prove the guard bites. A slug named after a real route
    segment MUST be reported, or the assertion above is decorative."""
    db = str(tmp_path / "chat_log.db")
    real_segment = sorted(ps.route_segments(appmod.app.url_map))[0]
    _seed(db, [real_segment])
    reserved = ps.route_segments(appmod.app.url_map)
    collisions = sorted(_published_slugs(db) & reserved)
    assert collisions == [real_segment]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_slug_route_collision.py -v`
Expected: FAIL. `dashboard.practitioner_slugs.init_tables` exists from Task 3, so the failure should be an import or assertion error only if Tasks 1-3 are incomplete. If Tasks 1-3 are done, **this test passes immediately** - that is expected and correct for `test_no_route_shadows_a_published_slug`. Confirm `test_guard_detects_a_planted_collision` passes too; if it does not, the guard is broken and must be fixed before proceeding.

- [ ] **Step 3: No implementation needed**

This task ships a test only. The behavior it guards is provided by Task 2.

- [ ] **Step 4: Run the full slug test set**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_slug_route_collision.py tests/test_practitioner_slugs.py -v`
Expected: PASS, both files.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add tests/test_slug_route_collision.py
git commit -m "test(slugs): guard that no route shadows a published practitioner slug"
```

---

### Task 5: The host-gated canonical route

Werkzeug already prefers static rules over dynamic ones regardless of registration order, so `/portal` beats `/<slug>` on its own. Registering last is belt-and-braces; the collision guard from Task 4 is the real protection.

**Files:**
- Modify: `app.py` (add after the `/p/<slug>` handler at `app.py:18609`)
- Test: `tests/test_practitioner_site_routes.py`

**Interfaces:**
- Consumes: `ps.resolve` from Task 3; `app._on_portal_host()` (`app.py:392`); `app._public_surface_enabled()`.
- Produces: Flask endpoint `practitioner_site` at rule `/<slug>`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py -v`
Expected: FAIL. `/mary-boyd` returns 404 because no such route exists yet.

- [ ] **Step 3: Write minimal implementation**

Add to `app.py`, immediately after the `practitioner_storefront` handler (`app.py:18609`):

```python
@app.route("/<slug>")
def practitioner_site(slug):
    """Practitioner site at myhealingoasis.com/<slug>.

    Host-gated: this catch-all exists only on the portal host, so it can never
    shadow a route on the funnel domain. Werkzeug already prefers static rules,
    so named routes win here too; tests/test_slug_route_collision.py is the
    guard that keeps it that way as routes are added.

    Serves the existing storefront page unchanged. Server-rendering and lifting
    noindex are section 5 of the spec, not this route.
    """
    if not _on_portal_host():
        return ("", 404)
    if not _public_surface_enabled():
        return ("", 404)
    from dashboard import practitioner_slugs as _ps
    s = _ps.normalize(slug)
    try:
        _ps.check_shape(s)
    except _ps.SlugError:
        return ("", 404)
    with db.connect(LOG_DB) as cx:
        kind, canonical = _ps.resolve(cx, s)
    if kind == "alias":
        return redirect(f"/{canonical}", code=301)
    if kind != "canonical":
        return ("", 404)
    resp = send_from_directory(STATIC, "practitioner-storefront.html")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.set_cookie("rm_ref", canonical, max_age=90 * 24 * 3600,
                    samesite="Lax", secure=request.is_secure)
    return resp
```

The alias branch is exercised by Task 6; it is written here because both branches live in one handler.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Verify no existing storefront test regressed**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_public_surface_routes.py tests/test_public_surface.py tests/test_public_surface_entrypoints.py -v`
Expected: PASS. These use the default `localhost` host, so `_on_portal_host()` is False and `/p/<slug>` behavior is untouched.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_site_routes.py
git commit -m "feat(site): host-gated practitioner site route at /<slug>"
```

---

### Task 6: Alternate redirects and the legacy /p/<slug> redirect

**Files:**
- Modify: `app.py` (the `practitioner_storefront` handler at `app.py:18609`)
- Test: `tests/test_practitioner_site_routes.py`

**Interfaces:**
- Consumes: `practitioner_site` from Task 5; `ps.claim_alias`, `ps.resolve` from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_site_routes.py

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
    """Alternates redirect; they never render, so they cannot duplicate content."""
    _claim(appmod.LOG_DB, "boyd-coaching")
    r = client.get("/boyd-coaching", base_url=f"http://{PORTAL_HOST}")
    assert b"<html" not in r.data.lower()


def test_legacy_p_slug_301s_to_canonical_on_portal_host(client):
    r = client.get("/p/mary-boyd", base_url=f"http://{PORTAL_HOST}")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/mary-boyd")


def test_legacy_p_slug_still_serves_on_the_funnel_host(client):
    """Old links must never break. /p/<slug> on illtowell.com is untouched."""
    r = client.get("/p/mary-boyd", base_url=f"http://{FUNNEL_HOST}")
    assert r.status_code == 200
    assert r.headers.get("X-Robots-Tag") == "noindex"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py -v -k "alias or legacy"`
Expected: `test_legacy_p_slug_301s_to_canonical_on_portal_host` FAILS with 200 instead of 301. The two alias tests should already PASS from the branch written in Task 5.

- [ ] **Step 3: Write minimal implementation**

At the top of the existing `practitioner_storefront` handler in `app.py`, immediately after the slug regex check and before the storefront lookup, insert:

```python
    # On the portal host the canonical URL is /<slug>; /p/<slug> is legacy and
    # 301s there so a printed or texted old link keeps working and search
    # engines consolidate on one URL. On the funnel host this route is unchanged.
    if _on_portal_host():
        return redirect(f"/{slug}", code=301)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Verify no existing storefront test regressed**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_public_surface_routes.py tests/test_no_legacy_storefront_links.py tests/test_superseded_storefront.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add app.py tests/test_practitioner_site_routes.py
git commit -m "feat(site): 301 alternates and legacy /p/<slug> to the canonical URL"
```

---

### Task 7: Correct the storefront URL shown to practitioners

`static/practitioner-settings.html:153` currently tells practitioners their storefront lives at `illtowell.com/p/your-slug`. That contradicts `PORTAL_BASE_URL` and is now wrong twice over: wrong host and wrong path.

**Files:**
- Modify: `static/practitioner-settings.html:153`
- Test: `tests/test_practitioner_site_routes.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_practitioner_site_routes.py
import pathlib


def test_settings_page_shows_the_current_storefront_url():
    """The settings copy must not advertise the retired illtowell.com/p/ form."""
    html = pathlib.Path(
        appmod.STATIC, "practitioner-settings.html").read_text(encoding="utf-8")
    assert "illtowell.com/p/" not in html
    assert "myhealingoasis.com/" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py::test_settings_page_shows_the_current_storefront_url -v`
Expected: FAIL on `assert "illtowell.com/p/" not in html`.

- [ ] **Step 3: Write minimal implementation**

In `static/practitioner-settings.html`, replace the storefront URL text on line 153:

```
        myhealingoasis.com/your-slug. Only what you save here is published — nothing is
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-deploy-chat-a191e6ab && doppler run --project remedy-match --config dev -- python3 -m pytest tests/test_practitioner_site_routes.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-deploy-chat-a191e6ab
git add static/practitioner-settings.html tests/test_practitioner_site_routes.py
git commit -m "fix(settings): show the current myhealingoasis.com storefront URL"
```

---

## Done criteria

- `myhealingoasis.com/mary-boyd` serves Mary's storefront; `illtowell.com/mary-boyd` 404s.
- An alternate 301s to the canonical and never renders content.
- `/p/<slug>` 301s on the portal host and is unchanged on the funnel host.
- The collision guard passes, and its planted-collision companion proves it bites.
- No existing public-surface test regressed.

## Deployment note

`PORTAL_BASE_URL` is already `https://myhealingoasis.com` in prd, so `_on_portal_host()` becomes live the moment this merges. There is no flag flip and therefore no second deploy for this plan. Before merging, confirm `mary-boyd` is actually free in the production `affiliate_signups` table and that Mary does not already hold a row with a different slug. That table lives in the sqlite LOG_DB on Render's persistent disk, not in Postgres, so it cannot be checked from a developer machine.
