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


def _page_slug_taken_bare(cx, candidate):
    """True iff `candidate` equals any practitioner's affiliate_signups.page_slug,
    with no self-exclusion.

    Used only by claim_alias. There is no "claimant's own row" to exempt the
    way page_slug_is_taken exempts one, because this check is symmetric with
    the existing canonical check right above its call site:
    slug_is_taken(cx, a) already refuses an alias equal to ANY practitioner's
    affiliate slug -- including the claimant's own canonical slug -- with no
    self-exemption; aliasing your own canonical to itself is refused today.
    An alias equal to the claimant's own page_slug is just as redundant, so it
    is refused the same way, bare.
    """
    init_page_slug(cx)
    row = cx.execute("SELECT 1 FROM affiliate_signups WHERE page_slug=?",
                     (normalize(candidate),)).fetchone()
    return row is not None


def claim_alias(cx, canonical, alias, reserved):
    """Reserve `alias` as a redirect to `canonical`. Raises SlugError if the
    alias is malformed, reserved, already an alias, anyone's canonical slug,
    or anyone's page slug.

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
    if _page_slug_taken_bare(cx, a):
        raise SlugError(f"'{a}' is already a practitioner's public URL")
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


# ── Page slug: the practitioner-chosen public URL ────────────────────────────
#
# A practitioner's affiliate_signups.slug is her ATTRIBUTION key: stored lead
# rows carry utm_source=<slug>, the printed Rebrandly shortlink is
# truly.vip/<slug>, and ?ref= cookies hold it for 90 days. Renaming it orphans
# all of that, so it is never written by this feature.
#
# page_slug is a separate column holding the name that belongs in her URL bar.
# It is ALWAYS POPULATED: a row that has made no choice carries its own
# affiliate slug, so every row's page_slug IS its effective public URL. When
# she picks a different one, IT becomes canonical and the affiliate slug
# becomes a legacy URL that keeps working forever.
#
# Always-populated is not cosmetic; it is what makes the invariant a database
# fact. With page_slug nullable, the unique index on it enforced nothing
# useful, because NULLs are distinct on both backends: with Mary's page_slug
# NULL, `UPDATE affiliate_signups SET page_slug='mary-boyd' WHERE
# slug='remedy-match'` collided with no VALUE and succeeded, and /mary-boyd
# then served Glen's page. Only the validator's read-then-write stood in the
# way, and that has a real race window on Postgres. Populated, Glen's claim
# collides with Mary's OWN row and the database refuses it.
#
# This is NOT the alias feature above. An alias points an extra name at an
# existing canonical and redirects to it. A page_slug makes a chosen name BE
# the canonical. The two share one namespace, and each guard reads the
# other's storage: page_slug_is_taken also checks practitioner_slug_aliases,
# and claim_alias also checks affiliate_signups.page_slug. Without that, one
# mechanism could silently claim a name the other had already published.

_PAGE_INIT_DONE = set()             # DB identities whose page_slug DDL has run
_PAGE_INIT_LOCK = threading.Lock()


def _try(cx, sql):
    """Run one DDL statement, tolerating "already exists".

    Each statement gets its own commit/rollback. On Postgres a failed statement
    poisons the whole transaction, so an ALTER that raises DuplicateColumn
    would take the CREATE INDEX after it down with it and the index would
    never be created. Same try/except Exception idiom as
    dashboard.practitioner_booking.init_tables, one transaction per statement.
    """
    try:
        cx.execute(sql)
        cx.commit()
        return True
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return False


def init_page_slug(cx):
    """Add affiliate_signups.page_slug and its unique index. Idempotent.

    Run ONCE per process per database, for the reason init_tables gives: every
    reader below calls this, and on the portal host a practitioner lookup is
    reached by every unmatched root path, including every bot probe. Per-request
    DDL in a pooled Postgres connection is what that guard prevents.

    The cache is only populated once the column is actually present. A call
    made before affiliate_signups exists must not mark the database done, or
    the column would stay dark for the life of the process.
    """
    key = _db_identity(cx)
    if key is not None:
        with _PAGE_INIT_LOCK:
            if key in _PAGE_INIT_DONE:
                return
    _try(cx, "ALTER TABLE affiliate_signups ADD COLUMN page_slug TEXT")
    # Backfill BEFORE the index, never after. A NULL page_slug is a row with no
    # effective URL under the index, which is exactly the hole the reviewer
    # drove through; and creating a unique index over a table still holding
    # NULLs cannot fail, so a later backfill would be the statement that
    # discovers a duplicate -- after the index that was supposed to prevent it
    # already exists. Idempotent by the WHERE clause, so it is safe on every
    # process start.
    _try(cx, "UPDATE affiliate_signups SET page_slug = slug"
             " WHERE page_slug IS NULL")
    # The backstop against a concurrent claim: validate_page_slug's
    # read-then-write has a race window that only the database can close. With
    # every row populated, this single-column index says "one effective public
    # URL, one practitioner" -- and says it about rows that have chosen nothing
    # as much as rows that have.
    _try(cx, "CREATE UNIQUE INDEX IF NOT EXISTS ux_affiliate_page_slug"
             " ON affiliate_signups(page_slug)")
    if key is None:
        return
    try:
        present = db.column_exists(cx, "affiliate_signups", "page_slug")
    except db.Error:
        present = False
    if present:
        with _PAGE_INIT_LOCK:
            _PAGE_INIT_DONE.add(key)


def canonical_slug_for(cx, affiliate_slug):
    """The slug that belongs in this practitioner's public URL: her page_slug
    when she has chosen one, otherwise her affiliate slug. '' if unknown.

    Every site that PRINTS a practitioner URL goes through here, so a rename
    reaches all of them at once.
    """
    init_page_slug(cx)
    s = normalize(affiliate_slug)
    if not s:
        return ""
    row = cx.execute(
        "SELECT slug, page_slug FROM affiliate_signups WHERE slug=?",
        (s,)).fetchone()
    if not row:
        return ""
    # COALESCE in Python, not SQL, so it also covers ''. page_slug is meant to
    # be populated for every row, but "meant to be" is only true while every
    # writer honours it. If one is ever missed, this hands back her affiliate
    # slug instead of an empty URL. Defence in depth behind the backfill and
    # the writers, never the mechanism they replace.
    return normalize(row[1]) or (row[0] or "")


def page_slug_is_taken(cx, candidate, *, excluding_affiliate_slug=None):
    """True iff `candidate` is ANY practitioner's affiliate slug, page slug, or
    published ALIAS, at ANY status, ignoring the claimant's own row.

    Deliberately broader than what serving looks at, for the reason
    slug_is_taken already gives: claiming is about the whole namespace, serving
    is about approved practitioners only. A page_slug allowed to take a PENDING
    practitioner's slug would put two owners on one URL the moment she is
    approved, and a published URL must never break that way. The same is true
    of an alias: page_slug and practitioner_slug_aliases.alias are two
    mechanisms writing into ONE namespace, so a page_slug candidate that
    matches a published alias must be refused too, or that alias would stop
    resolving to its owner.

    Her own alias is exempted the same way her own row is. An alias she
    already owns already resolves to her -- setting her page_slug to that same
    string hands her no new name and collides with nobody. It is "reclaiming
    your own", the same case excluding_affiliate_slug exists for on the
    affiliate_signups side.

    Both columns and the alias table, because they are one namespace:
    whichever mechanism a request matches, it must match exactly one owner.
    """
    init_page_slug(cx)
    init_tables(cx)
    c = normalize(candidate)
    if not c:
        return False
    sql = "SELECT 1 FROM affiliate_signups WHERE (slug=? OR page_slug=?)"
    params = [c, c]
    own = normalize(excluding_affiliate_slug)
    if own:
        sql += " AND slug<>?"
        params.append(own)
    if cx.execute(sql, tuple(params)).fetchone() is not None:
        return True
    alias_sql = "SELECT 1 FROM practitioner_slug_aliases WHERE alias=?"
    alias_params = [c]
    if own:
        alias_sql += " AND canonical_slug<>?"
        alias_params.append(own)
    return cx.execute(alias_sql, tuple(alias_params)).fetchone() is not None


def validate_page_slug(cx, candidate, *, owner_affiliate_slug, reserved):
    """Normalize and check `candidate`, returning the value to store.

    Raises SlugError with a message a practitioner can act on. `reserved` comes
    from reserved_for(app.url_map) at the call site, never a list written here:
    a slug that shadows a live route is a page she could publish and never
    reach.
    """
    s = normalize(candidate)
    check_shape(s)
    check_not_reserved(s, reserved)
    if page_slug_is_taken(cx, s, excluding_affiliate_slug=owner_affiliate_slug):
        raise SlugError(f"'{s}' is already in use. Please choose another.")
    return s


def set_page_slug(cx, affiliate_slug, candidate, *, reserved):
    """Give `affiliate_slug`'s practitioner the public URL `candidate`.

    An empty or None candidate CLEARS the choice, restoring her AFFILIATE SLUG
    as her page_slug -- not NULL. A NULL would take her row back out of the
    unique index's reach, and a row outside the index is a row another
    practitioner can be pointed at by any write that skips the validator.
    Writing her own slug is also unambiguously free: page_slug_is_taken refuses
    to give anyone else a page slug equal to it.

    Returns the stored page slug, which after a clear is the affiliate slug.
    "No vanity choice" and "vanity choice equal to the affiliate slug" are the
    same state now, so there is nothing for a '' return to distinguish. Never
    touches affiliate_signups.slug.
    """
    init_page_slug(cx)
    owner = normalize(affiliate_slug)
    if cx.execute("SELECT 1 FROM affiliate_signups WHERE slug=?",
                  (owner,)).fetchone() is None:
        # Silently updating zero rows would report success and then show her a
        # URL that does not exist.
        raise SlugError("that practitioner account was not found")

    if not normalize(candidate):
        s = owner            # clearing == "my public URL is my affiliate slug"
    else:
        s = validate_page_slug(cx, candidate, owner_affiliate_slug=owner,
                               reserved=reserved)
    try:
        cx.execute("UPDATE affiliate_signups SET page_slug=? WHERE slug=?",
                   (s, owner))
        cx.commit()
    except db.IntegrityError as e:       # concurrent claim won the race
        try:
            cx.rollback()
        except Exception:
            pass
        raise SlugError(f"'{s}' is already in use. Please choose another.") from e
    return s


def resolve_page(cx, requested):
    """Resolve a requested URL slug to (kind, canonical, affiliate_slug).

    kind is 'canonical' when the request is already the right URL, 'legacy'
    when it is the affiliate slug of a practitioner who has chosen a different
    page slug, and '' when nobody owns it.

    page_slug is matched FIRST. It cannot be shadowed, because page_slug_is_taken
    refuses a page slug that collides with anyone's slug, page_slug, or
    published alias.

    Status is deliberately not filtered here. Serving is gated downstream by
    public_surface.build_practitioner_storefront, which is approved-only and
    fails closed; the namespace guard above is what stops a pending row from
    ever claiming an approved practitioner's URL in the first place.

    affiliate_slug is returned alongside so callers can key analytics and
    attribution on it. Those must never move to the display slug, or changing
    a vanity URL would split a practitioner's view history.
    """
    init_page_slug(cx)
    r = normalize(requested)
    if not r:
        return ("", "", "")
    row = cx.execute(
        "SELECT slug, page_slug FROM affiliate_signups WHERE page_slug=?",
        (r,)).fetchone()
    if row:
        return ("canonical", r, row[0] or "")
    row = cx.execute(
        "SELECT slug, page_slug FROM affiliate_signups WHERE slug=?",
        (r,)).fetchone()
    if row:
        page = normalize(row[1])
        if page and page != r:
            return ("legacy", page, row[0] or "")
        # `not page` is the COALESCE fallback: every row is SUPPOSED to carry a
        # page_slug, but if a writer we never found inserts one without it,
        # she must still serve at her affiliate slug rather than 404. Defence
        # in depth behind the backfill and the writers, not a substitute.
        return ("canonical", r, row[0] or "")
    return ("", "", "")
