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

import datetime
import re
import threading

from dashboard import db

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


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


_INIT_DONE = set()                  # DB identities whose alias DDL has already run
_INIT_LOCK = threading.Lock()


def _db_identity(cx):
    """A stable identity for the database behind `cx`, or None when it cannot be
    determined cheaply -- in which case the DDL simply runs again, which is
    harmless because it is idempotent.

    Keyed on the DATABASE, never on a bare process-wide boolean: the test suite
    points LOG_DB at a fresh temp file per test, and a boolean would leave every
    database after the first without its table. An in-memory SQLite database
    reports an empty path but is a distinct database per connection, so it
    returns None and is never cached.
    """
    try:
        if db.backend_of(cx) == "postgres":
            row = cx.execute("SELECT current_schema()").fetchone()
            return ("postgres", row[0]) if row and row[0] else None
        row = cx.execute("PRAGMA database_list").fetchone()
        path = (row[2] or "") if row else ""
        return ("sqlite", path) if path else None
    except db.Error:
        return None


def init_tables(cx):
    """Create the alias table. Idempotent; safe to call on every read path,
    matching dashboard.referrals.init_tables -- but run ONCE per process per
    database, not once per call.

    alias_owner() calls this on every canonical miss, and on the portal host a
    canonical miss is reached by every unmatched root path: every bot probe of
    /admin, /wordpress, /.env. Per-request CREATE TABLE + COMMIT is a WAL write
    on SQLite and per-request DDL in a pooled connection on Postgres, where
    concurrent CREATE TABLE IF NOT EXISTS can raise DuplicateTable. The probe
    that replaces it is a read-only PRAGMA with no commit.
    """
    key = _db_identity(cx)
    if key is not None:
        with _INIT_LOCK:
            if key in _INIT_DONE:
                return
    cx.execute("CREATE TABLE IF NOT EXISTS practitioner_slug_aliases ("
               "alias TEXT PRIMARY KEY, canonical_slug TEXT NOT NULL,"
               " created_at TEXT NOT NULL)")
    cx.commit()
    if key is not None:
        with _INIT_LOCK:
            _INIT_DONE.add(key)


def canonical_exists(cx, slug):
    """True iff `slug` is an APPROVED practitioner's canonical slug."""
    row = cx.execute(
        "SELECT 1 FROM affiliate_signups WHERE slug=? AND status='approved'",
        (normalize(slug),)).fetchone()
    return row is not None


def slug_is_taken(cx, slug):
    """True iff `slug` is ANY practitioner's canonical slug, at any status.

    Deliberately broader than canonical_exists: claiming is about the whole
    namespace, serving is about approved practitioners only. If an alias could
    take a PENDING practitioner's slug, then on that practitioner's approval
    resolve() -- which checks canonical first -- would silently start shadowing
    a published alias. A published URL must never break that way.
    """
    row = cx.execute("SELECT 1 FROM affiliate_signups WHERE slug=?",
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
    if slug_is_taken(cx, a):
        raise SlugError(f"'{a}' is already a practitioner's canonical slug")
    if alias_owner(cx, a):
        raise SlugError(f"'{a}' is already claimed as an alias")
    # Validate the TARGET too. It is written into the table and later handed to
    # redirect(f"/{canonical}"), so an unchecked value such as "//evil.com"
    # would be an open-redirect target stored in the database. resolve()'s
    # independent canonical_exists() re-check is a second line of defence, not
    # a substitute for refusing the write.
    check_shape(c)
    if not canonical_exists(cx, c):
        raise SlugError(f"'{c}' is not an approved practitioner slug")
    try:
        cx.execute("INSERT INTO practitioner_slug_aliases"
                   " (alias, canonical_slug, created_at) VALUES (?,?,?)",
                   (a, c, _now()))
        cx.commit()
    except db.IntegrityError as e:           # concurrent claim won the race
        raise SlugError(f"'{a}' is already claimed as an alias") from e
