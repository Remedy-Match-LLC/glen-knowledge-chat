"""Unit tests for the practitioner slug namespace. Imports no Flask app."""
import os
import sqlite3

import pytest
from werkzeug.routing import Map, Rule

from dashboard import affiliate_dashboard
from dashboard import db
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


def test_slug_is_taken_covers_any_status(tmp_path):
    """slug_is_taken is deliberately broader than canonical_exists: it must see
    a PENDING practitioner's slug too, since claiming is about the whole
    namespace, not just who may currently be served."""
    cx = _cx(tmp_path)
    assert ps.slug_is_taken(cx, "mary-boyd") is True
    assert ps.slug_is_taken(cx, "pending-pat") is True
    assert ps.slug_is_taken(cx, "nobody") is False


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


def test_claim_alias_converts_a_real_integrity_violation_to_slug_error(tmp_path, monkeypatch):
    """Exercises the try/except db.IntegrityError backstop in claim_alias with a
    REAL sqlite PRIMARY KEY violation, not a mocked exception.

    In production, the race is: two processes both call alias_owner(cx, a),
    both see it unclaimed, and both proceed toward the INSERT before either
    commits. Here we simulate that window by patching the pre-check seam
    (alias_owner) to report "unclaimed" while the alias row genuinely already
    exists in practitioner_slug_aliases. claim_alias's own pre-check is
    therefore fooled exactly as it would be in the real race, and the
    exception that stops it comes from sqlite itself enforcing the alias
    PRIMARY KEY on the real table -- not from anything we constructed.

    The canonical is 'mary-boyd', an APPROVED practitioner, deliberately: since
    claim_alias also validates its canonical argument, a pending or unknown one
    would be refused BEFORE the INSERT and this test would pass without ever
    reaching the backstop it exists to cover. The __cause__ assertion is what
    tells the two apart -- the pre-checks and the backstop raise the same
    message, but only the backstop chains a db.IntegrityError.
    """
    cx = _cx(tmp_path)
    ps.init_tables(cx)
    cx.execute(
        "INSERT INTO practitioner_slug_aliases (alias, canonical_slug, created_at)"
        " VALUES ('shared-name', 'mary-boyd', '2026-01-01T00:00:00+00:00')")
    cx.commit()

    monkeypatch.setattr(ps, "alias_owner", lambda cx, alias: "")

    with pytest.raises(ps.SlugError) as exc:
        ps.claim_alias(cx, "mary-boyd", "shared-name", frozenset())
    assert isinstance(exc.value.__cause__, db.IntegrityError)


def test_claim_alias_rejects_a_malformed_canonical(tmp_path):
    """The canonical is written into the table and later fed to redirect() as
    f"/{canonical}". An unvalidated value like '//evil.com' would be an
    open-redirect target sitting in the database. Reject it at the writer;
    resolve()'s independent canonical_exists() re-check is a second line of
    defence, not the only one."""
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "//evil.com", "my-alias", frozenset())
    assert ps.alias_owner(cx, "my-alias") == ""


def test_claim_alias_rejects_a_canonical_that_is_not_an_approved_practitioner(tmp_path):
    """A well-shaped canonical that nobody owns would create an alias that
    resolves to nothing -- a published URL that 404s from birth."""
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "nobody-here", "my-alias", frozenset())
    assert ps.alias_owner(cx, "my-alias") == ""


def test_claim_alias_rejects_a_pending_practitioner_as_canonical(tmp_path):
    """canonical_exists is approved-only, and so is serving. Pointing an alias
    at a pending practitioner would publish a redirect to a 404."""
    cx = _cx(tmp_path)
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "pending-pat", "pats-clinic", frozenset())
    assert ps.alias_owner(cx, "pats-clinic") == ""


class _CountingCx:
    """A connection wrapper that records every SQL string it is asked to run."""

    def __init__(self, cx):
        self._cx = cx
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self._cx.execute(sql, params)

    def commit(self):
        return self._cx.commit()

    def rollback(self):
        return self._cx.rollback()


def test_init_tables_issues_its_ddl_once_per_database(tmp_path):
    """alias_owner() calls init_tables on every canonical miss, and on the
    portal host that miss is reached by every unmatched root path -- every bot
    probe of /admin, /wordpress, /.env. CREATE TABLE + COMMIT per public
    request is per-request DDL in a pooled Postgres connection, where
    concurrent CREATE TABLE IF NOT EXISTS can raise DuplicateTable."""
    cx = _CountingCx(sqlite3.connect(str(tmp_path / "once.db")))
    for _ in range(3):
        ps.init_tables(cx)
    creates = [s for s in cx.sql if s.startswith("CREATE TABLE")]
    assert len(creates) == 1, cx.sql


def test_init_tables_is_keyed_on_the_database_not_a_process_flag(tmp_path):
    """A bare boolean would leave the SECOND database without its table, which
    is every test after the first that points LOG_DB at a fresh temp file."""
    ps.init_tables(sqlite3.connect(str(tmp_path / "first.db")))
    second = sqlite3.connect(str(tmp_path / "second.db"))
    ps.init_tables(second)
    assert second.execute("SELECT * FROM practitioner_slug_aliases").fetchall() == []


def test_init_tables_never_caches_an_in_memory_database():
    """Every :memory: connection is a DIFFERENT database that reports the same
    empty path, so it must not be cached under that path."""
    for _ in range(2):
        cx = sqlite3.connect(":memory:")
        ps.init_tables(cx)
        assert cx.execute("SELECT * FROM practitioner_slug_aliases").fetchall() == []


# ─────────────────────────────────────────────────────────────────────────────
# page_slug: the practitioner-chosen public URL, separate from the affiliate
# slug that carries attribution.
# ─────────────────────────────────────────────────────────────────────────────

# affiliate_signups and referral_sources as app.py::_init_referral_tables
# creates them in production, copied verbatim from that initializer including
# its ALTERed columns. This module deliberately imports no Flask app, so
# app.py's initializer cannot be called from here; the schema is reproduced
# rather than invented, and the ROWS are written by the real production writer
# (affiliate_dashboard.ensure_affiliate), never by a hand-written INSERT.
# referral_sources is present because ensure_affiliate writes to it too, and it
# swallows its own exceptions: without that table the writer would return None
# and every fixture would be silently empty.
_PROD_DDL = """
CREATE TABLE IF NOT EXISTS affiliate_signups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    organization TEXT DEFAULT '',
    website      TEXT DEFAULT '',
    promo_method TEXT DEFAULT '',
    slug         TEXT NOT NULL UNIQUE,
    token        TEXT NOT NULL UNIQUE,
    status       TEXT DEFAULT 'approved',
    notes        TEXT DEFAULT '',
    referred_by  TEXT DEFAULT '',
    short_url    TEXT DEFAULT '',
    gifting_activated_at TEXT
);
CREATE TABLE IF NOT EXISTS referral_sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL UNIQUE,
    description  TEXT DEFAULT '',
    utm_source   TEXT NOT NULL,
    utm_medium   TEXT DEFAULT 'referral',
    utm_campaign TEXT DEFAULT '',
    active       INTEGER DEFAULT 1
);
"""


@pytest.fixture
def cx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chat_log.db"))
    conn.executescript(_PROD_DDL)
    conn.commit()
    ps.init_page_slug(conn)      # the module's OWN DDL adds page_slug
    return conn


def _seed(cx, slug, status="approved"):
    """Create a practitioner row through the real production writer.

    ensure_affiliate mints the slug from the name, so we assert it minted the
    one the test asked for. Without that assertion a minting change would
    quietly point every test at a different row than it names.
    """
    row = affiliate_dashboard.ensure_affiliate(
        cx, f"{slug}@example.com", name=slug.replace("-", " "))
    assert row and row["slug"] == slug, row
    if status != "approved":
        cx.execute("UPDATE affiliate_signups SET status=? WHERE slug=?",
                   (status, slug))
        cx.commit()
    return row


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
    """Clearing writes the AFFILIATE SLUG back into page_slug, not NULL. The
    stored column is asserted directly, not just the resolution: a NULL would
    resolve identically while quietly dropping her row out of the unique index
    that is the only real guard on "one URL, one practitioner"."""
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    assert ps.set_page_slug(cx, "remedy-match", "", reserved=frozenset()) == "remedy-match"
    stored = cx.execute("SELECT page_slug FROM affiliate_signups"
                        " WHERE slug='remedy-match'").fetchone()[0]
    assert stored == "remedy-match"
    assert ps.resolve_page(cx, "remedy-match") == ("canonical", "remedy-match", "remedy-match")
    assert ps.resolve_page(cx, "dr-glen") == ("", "", "")


def test_a_cleared_row_still_occupies_the_unique_index(cx):
    """The point of writing the affiliate slug rather than NULL. After Mary
    clears her vanity slug, a write that bypasses the validator entirely must
    still be unable to hand her URL to Glen."""
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    ps.set_page_slug(cx, "mary-boyd", "", reserved=frozenset())
    with pytest.raises(db.IntegrityError):
        cx.execute("UPDATE affiliate_signups SET page_slug='mary-boyd'"
                   " WHERE slug='remedy-match'")
    cx.rollback()
    assert ps.resolve_page(cx, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")


def test_init_page_slug_is_idempotent(cx):
    ps.init_page_slug(cx); ps.init_page_slug(cx)   # must not raise on the second call


def test_a_page_slug_may_not_shadow_a_pending_practitioners_slug(cx):
    """Same reasoning as slug_is_taken: claiming is about the whole namespace,
    serving is about approved practitioners only. If a page_slug could take a
    PENDING practitioner's affiliate slug, that practitioner's approval would
    later put two owners on one URL."""
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="pending-pat", status="pending")
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "pending-pat", reserved=frozenset())


def test_a_pending_practitioners_page_slug_also_blocks_the_namespace(cx):
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="pending-pat", status="pending")
    ps.set_page_slug(cx, "pending-pat", "the-coach", reserved=frozenset())
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "the-coach", reserved=frozenset())


def test_page_slug_is_taken_sees_both_columns_at_any_status(cx):
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="pending-pat", status="pending")
    ps.set_page_slug(cx, "pending-pat", "the-coach", reserved=frozenset())
    assert ps.page_slug_is_taken(cx, "remedy-match") is True     # someone's slug
    assert ps.page_slug_is_taken(cx, "pending-pat") is True      # pending slug
    assert ps.page_slug_is_taken(cx, "the-coach") is True        # someone's page_slug
    assert ps.page_slug_is_taken(cx, "nobody") is False
    # The claimant's own row never counts against them.
    assert ps.page_slug_is_taken(
        cx, "the-coach", excluding_affiliate_slug="pending-pat") is False


def test_claiming_a_page_slug_equal_to_anothers_alias_is_refused(cx):
    """The two namespaces guard each other: a vanity URL cannot silently
    shadow a published alias that already resolves somewhere else."""
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="mary-boyd")
    ps.claim_alias(cx, "mary-boyd", "healing-oasis-hilo", frozenset())
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "remedy-match", "healing-oasis-hilo",
                         reserved=frozenset())
    # Mary's alias still resolves to Mary -- the refusal above must not have
    # written anything.
    assert ps.alias_owner(cx, "healing-oasis-hilo") == "mary-boyd"


def test_claiming_an_alias_equal_to_anothers_page_slug_is_refused(cx):
    """The other direction: an alias cannot be claimed on top of a name
    someone already published as their vanity URL."""
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "remedy-match", "the-coach", frozenset())
    assert ps.alias_owner(cx, "the-coach") == ""


def test_reclaiming_your_own_alias_as_a_page_slug_is_not_a_collision(cx):
    """An alias she already owns already resolves to her, so setting her
    page_slug to that same string hands her no new name and must not be
    treated as a collision -- the alias-side twin of
    test_reclaiming_your_own_page_slug_is_not_a_collision."""
    _seed(cx, slug="mary-boyd")
    ps.claim_alias(cx, "mary-boyd", "healing-oasis-hilo", frozenset())
    assert ps.set_page_slug(cx, "mary-boyd", "healing-oasis-hilo",
                            reserved=frozenset()) == "healing-oasis-hilo"


def test_claiming_your_own_page_slug_as_an_alias_is_refused(cx):
    """The claim-path guard has no self-exemption, matching the existing
    (unwidened) behaviour of slug_is_taken: aliasing your own canonical slug
    to itself was already refused before this change, and aliasing your own
    page_slug to itself is refused the same way, bare."""
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "the-coach", reserved=frozenset())
    with pytest.raises(ps.SlugError):
        ps.claim_alias(cx, "remedy-match", "the-coach", frozenset())


def test_the_two_existing_features_still_work_for_non_colliding_cases(cx):
    """Sanity check that widening the guards did not fold the two mechanisms
    together: an alias and a page_slug that do not collide with anything
    still both work exactly as before."""
    _seed(cx, slug="remedy-match")
    _seed(cx, slug="mary-boyd")
    ps.claim_alias(cx, "remedy-match", "healing-oasis-hilo", frozenset())
    assert ps.resolve(cx, "healing-oasis-hilo") == ("alias", "remedy-match")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    assert ps.resolve_page(cx, "the-coach") == ("canonical", "the-coach", "mary-boyd")


def test_a_malformed_page_slug_is_refused(cx):
    _seed(cx, slug="remedy-match")
    for bad in ("Bad--Shape", "ab", "dr glen", "dr_glen"):
        with pytest.raises(ps.SlugError):
            ps.set_page_slug(cx, "remedy-match", bad, reserved=frozenset())
    assert ps.canonical_slug_for(cx, "remedy-match") == "remedy-match"


def test_setting_a_page_slug_for_a_practitioner_who_does_not_exist_is_refused(cx):
    """An UPDATE that matches no row would report success while writing
    nothing, and the settings page would show the practitioner a URL that
    does not exist."""
    with pytest.raises(ps.SlugError):
        ps.set_page_slug(cx, "nobody", "dr-glen", reserved=frozenset())


def test_canonical_slug_for_an_unknown_practitioner_is_empty(cx):
    assert ps.canonical_slug_for(cx, "nobody") == ""


def test_two_cleared_page_slugs_do_not_collide(cx):
    """Clearing writes each practitioner's OWN affiliate slug, which is unique
    by the affiliate_signups.slug constraint, so two clears can never be a
    duplicate under the page_slug index.

    Inverted from its original form, which asserted that clearing writes NULL
    (two NULLs being distinct under a unique index on both backends). NULL was
    what let a raw UPDATE hand one practitioner another's URL, so the reason
    this test passes has changed even though the assertions have not. Writing
    '' would still be the bug the original docstring named: two empty strings
    ARE a duplicate."""
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    ps.set_page_slug(cx, "remedy-match", "", reserved=frozenset())
    ps.set_page_slug(cx, "mary-boyd", None, reserved=frozenset())
    assert ps.resolve_page(cx, "remedy-match") == ("canonical", "remedy-match", "remedy-match")
    assert ps.resolve_page(cx, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")


def test_the_unique_index_is_the_backstop_against_a_concurrent_claim(cx):
    """validate_page_slug's read-then-write has a race window. The database
    index is what closes it, so assert the index really exists by writing
    around the validator."""
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    with pytest.raises(db.IntegrityError):
        cx.execute("UPDATE affiliate_signups SET page_slug='the-coach'"
                   " WHERE slug='remedy-match'")


def test_a_lost_race_surfaces_as_a_slug_error_not_an_integrity_error(cx):
    """The practitioner who loses the race must see the same readable refusal
    as the one who was merely second. Simulates the window by patching the
    pre-check seam so the real index is what stops the write."""
    _seed(cx, slug="remedy-match"); _seed(cx, slug="mary-boyd")
    ps.set_page_slug(cx, "mary-boyd", "the-coach", reserved=frozenset())
    import unittest.mock as _mock
    with _mock.patch.object(ps, "page_slug_is_taken", lambda *a, **k: False):
        with pytest.raises(ps.SlugError):
            ps.set_page_slug(cx, "remedy-match", "the-coach", reserved=frozenset())


def test_init_page_slug_issues_its_ddl_once_per_database(tmp_path):
    """Readers call init_page_slug on every request, exactly as alias_owner
    calls init_tables. Per-request ALTER TABLE in a pooled Postgres connection
    is the failure this guard exists to prevent."""
    raw = sqlite3.connect(str(tmp_path / "page_once.db"))
    raw.executescript(_PROD_DDL)
    raw.commit()
    counting = _CountingCx(raw)
    for _ in range(3):
        ps.init_page_slug(counting)
    alters = [s for s in counting.sql if s.startswith("ALTER TABLE")]
    assert len(alters) == 1, counting.sql


def test_init_page_slug_is_keyed_on_the_database_not_a_process_flag(tmp_path):
    """A bare boolean would leave the SECOND database without its column,
    which is every test after the first that points LOG_DB at a temp file."""
    for name in ("first.db", "second.db"):
        conn = sqlite3.connect(str(tmp_path / name))
        conn.executescript(_PROD_DDL)
        conn.commit()
        ps.init_page_slug(conn)
        assert db.column_exists(conn, "affiliate_signups", "page_slug") is True


# ─────────────────────────────────────────────────────────────────────────────
# page_slug is ALWAYS POPULATED. Every row's page_slug IS its effective public
# URL, defaulting to its own affiliate slug, so the single-column unique index
# enforces "one URL, one practitioner" at the database instead of leaving it to
# a validator's read-then-write.
# ─────────────────────────────────────────────────────────────────────────────


def _legacy_cx(tmp_path, name="legacy.db"):
    """A database as production looked BEFORE this feature: affiliate_signups
    holding rows and having NO page_slug column.

    These INSERTs are raw rather than going through the production writer, and
    that is the point: they stand for practitioners minted by the writer as it
    stood before page_slug existed, which is a writer no longer in the tree.
    init_page_slug's backfill is the only thing that can give them a page_slug,
    so this fixture is what isolates the backfill from the writers.
    """
    conn = sqlite3.connect(str(tmp_path / name))
    conn.executescript(_PROD_DDL)
    for slug in ("remedy-match", "mary-boyd"):
        conn.execute(
            "INSERT INTO affiliate_signups (created_at,name,email,slug,token,status)"
            " VALUES ('2026-01-01T00:00:00+00:00',?,?,?,?,'approved')",
            (slug.replace("-", " "), f"{slug}@example.com", slug, f"tok-{slug}"))
    conn.commit()
    assert db.column_exists(conn, "affiliate_signups", "page_slug") is False
    return conn


def test_a_raw_update_cannot_hand_a_legacy_row_another_practitioners_url(tmp_path):
    """The reviewer's exact reproduction, inverted.

    Two practitioners, neither having chosen a page slug. He then ran

        UPDATE affiliate_signups SET page_slug='mary-boyd' WHERE slug='remedy-match'

    and it SUCCEEDED, because with Mary's own page_slug NULL there was no
    colliding VALUE. resolve_page(cx, 'mary-boyd') then returned Glen's row and
    /mary-boyd served a different practitioner's page. The only thing standing
    in the way was validate_page_slug's SELECT, which this UPDATE bypasses
    entirely and which has a real race window on Postgres regardless.

    Once the backfill populates both rows, Mary's own page_slug is 'mary-boyd'
    and Glen's claim collides with HER row. Bypassing the validator is exactly
    what this test does, so what refuses the write can only be the database.
    """
    cx = _legacy_cx(tmp_path)
    ps.init_page_slug(cx)
    with pytest.raises(db.IntegrityError):
        cx.execute("UPDATE affiliate_signups SET page_slug='mary-boyd'"
                   " WHERE slug='remedy-match'")
    cx.rollback()
    assert ps.resolve_page(cx, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")
    assert ps.resolve_page(cx, "remedy-match") == ("canonical", "remedy-match", "remedy-match")


def test_a_raw_update_cannot_hand_a_freshly_written_row_anothers_url(tmp_path):
    """The same reproduction for rows created by the PRODUCTION WRITER after
    the column already exists, where the backfill has nothing to do. Backfill
    and writer are two independent ways for a row to end up populated, and a
    test that only covers one of them lets the other regress silently."""
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    conn.executescript(_PROD_DDL)
    conn.commit()
    ps.init_page_slug(conn)          # column exists, table empty: no backfill
    _seed(conn, slug="remedy-match"); _seed(conn, slug="mary-boyd")
    with pytest.raises(db.IntegrityError):
        conn.execute("UPDATE affiliate_signups SET page_slug='mary-boyd'"
                     " WHERE slug='remedy-match'")
    conn.rollback()
    assert ps.resolve_page(conn, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")


def test_the_production_writer_populates_page_slug(cx):
    """ensure_affiliate is the writer behind auto-enrolment. A new row it makes
    must carry page_slug = its own slug, or that row is outside the unique
    index and the test above is the only thing holding the invariant up."""
    _seed(cx, slug="mary-boyd")
    stored = cx.execute("SELECT page_slug FROM affiliate_signups"
                        " WHERE slug='mary-boyd'").fetchone()[0]
    assert stored == "mary-boyd"


def test_the_backfill_is_idempotent(tmp_path, monkeypatch):
    """init_page_slug runs on every process start. Running it twice over a
    table that already holds rows must leave every page_slug equal to its slug
    and raise nothing. The cache is cleared between calls so the DDL genuinely
    runs a second time rather than short-circuiting."""
    cx = _legacy_cx(tmp_path)
    for _ in range(2):
        monkeypatch.setattr(ps, "_PAGE_INIT_DONE", set())
        ps.init_page_slug(cx)
    rows = cx.execute("SELECT slug, page_slug FROM affiliate_signups"
                      " ORDER BY slug").fetchall()
    assert rows == [("mary-boyd", "mary-boyd"), ("remedy-match", "remedy-match")]


def test_the_backfill_leaves_a_chosen_page_slug_alone(tmp_path, monkeypatch):
    """`WHERE page_slug IS NULL` is what makes the backfill idempotent. Without
    it a second run would overwrite every vanity URL a practitioner has chosen
    with her affiliate slug -- silently unpublishing her page."""
    cx = _legacy_cx(tmp_path)
    ps.init_page_slug(cx)
    ps.set_page_slug(cx, "remedy-match", "dr-glen", reserved=frozenset())
    monkeypatch.setattr(ps, "_PAGE_INIT_DONE", set())
    ps.init_page_slug(cx)
    assert ps.canonical_slug_for(cx, "remedy-match") == "dr-glen"
    assert ps.resolve_page(cx, "dr-glen") == ("canonical", "dr-glen", "remedy-match")


def test_resolve_page_falls_back_for_a_row_left_null_by_some_writer(cx):
    """Defence in depth. "Always populated" is only true while every writer
    honours it; if one is ever missed, that practitioner must still serve at
    her affiliate slug rather than 404. The NULL here is written directly,
    standing in for a writer this change did not find."""
    _seed(cx, slug="mary-boyd")
    cx.execute("UPDATE affiliate_signups SET page_slug=NULL WHERE slug='mary-boyd'")
    cx.commit()
    assert ps.resolve_page(cx, "mary-boyd") == ("canonical", "mary-boyd", "mary-boyd")
    assert ps.canonical_slug_for(cx, "mary-boyd") == "mary-boyd"


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: a failed backfill must be loud, and the index it feeds must be
# understood for what it actually enforces.
# ─────────────────────────────────────────────────────────────────────────────


def test_the_backfill_warns_loudly_when_nulls_survive(tmp_path, monkeypatch, capsys):
    """Reproduces the exact hole this warning exists to close.

    Steady state: the column and unique index already exist from a prior
    successful init_page_slug run, and Glen has already renamed his public
    URL to 'mary-boyd' (a name nobody else holds yet). A second practitioner
    is then minted with the affiliate slug 'mary-boyd' -- inserted raw,
    standing in for a writer this change did not find, the same idiom
    _legacy_cx above uses -- and never gets a page_slug.

    On the next process start, init_page_slug's backfill tries to set her
    page_slug to her own slug, 'mary-boyd', which collides with Glen's
    ALREADY-CHOSEN page_slug. The UPDATE raises, _try swallows it, and
    CREATE UNIQUE INDEX IF NOT EXISTS -- already present -- has nothing to
    say about the NULL that survives. Without the warning this is a silent
    deploy with the invariant unenforced; Task 4 makes it loud instead.
    """
    conn = sqlite3.connect(str(tmp_path / "warn.db"))
    conn.executescript(_PROD_DDL)
    conn.commit()
    ps.init_page_slug(conn)   # steady state: column + index already exist

    _seed(conn, slug="remedy-match")
    ps.set_page_slug(conn, "remedy-match", "mary-boyd", reserved=frozenset())

    conn.execute(
        "INSERT INTO affiliate_signups"
        " (created_at,name,email,slug,token,status,page_slug)"
        " VALUES ('2026-01-01T00:00:00+00:00','Mary Boyd','mary-boyd@example.com',"
        " 'mary-boyd','tok-mary-boyd','approved',NULL)")
    conn.commit()

    monkeypatch.setattr(ps, "_PAGE_INIT_DONE", set())   # force the DDL to run again
    capsys.readouterr()                                  # discard setup noise
    ps.init_page_slug(conn)
    out = capsys.readouterr().out
    assert "WARNING" in out, out
    assert "1 affiliate_signups" in out, out

    remaining = conn.execute(
        "SELECT page_slug FROM affiliate_signups WHERE slug='mary-boyd'"
    ).fetchone()[0]
    assert remaining is None   # the backfill genuinely failed for this row


def test_the_backfill_is_silent_when_nulls_do_not_survive(tmp_path, capsys):
    """The common case -- a clean, first-ever backfill over legacy rows with
    no pre-existing page_slug collisions -- must print nothing. A warning
    that fires on every boot would train everyone to ignore it."""
    cx = _legacy_cx(tmp_path)
    capsys.readouterr()
    ps.init_page_slug(cx)
    out = capsys.readouterr().out
    assert "WARNING" not in out, out
    remaining = cx.execute(
        "SELECT COUNT(*) FROM affiliate_signups WHERE page_slug IS NULL"
    ).fetchone()[0]
    assert remaining == 0


def test_the_unique_index_tolerates_multiple_null_page_slugs(tmp_path):
    """NULLs are distinct on both backends' UNIQUE index semantics -- SQLite
    here, and Postgres in the skipif variant below. This is WHY page_slug
    must be ALWAYS POPULATED to mean anything: the index alone does not stop
    two never-backfilled, never-chosen rows from both sitting outside its
    reach at once. See the raw-UPDATE reproduction tests above for what an
    unpopulated row lets happen the moment someone DOES claim its name."""
    conn = sqlite3.connect(str(tmp_path / "nulls.db"))
    conn.executescript(_PROD_DDL)
    conn.execute("ALTER TABLE affiliate_signups ADD COLUMN page_slug TEXT")
    conn.execute("CREATE UNIQUE INDEX ux_affiliate_page_slug"
                 " ON affiliate_signups(page_slug)")
    for slug in ("remedy-match", "mary-boyd"):
        conn.execute(
            "INSERT INTO affiliate_signups"
            " (created_at,name,email,slug,token,status,page_slug)"
            " VALUES ('2026-01-01T00:00:00+00:00',?,?,?,?,'approved',NULL)",
            (slug.replace("-", " "), f"{slug}@example.com", slug, f"tok-{slug}"))
    conn.commit()   # must not raise: two NULLs are not a duplicate
    rows = conn.execute(
        "SELECT page_slug FROM affiliate_signups ORDER BY slug").fetchall()
    assert rows == [(None,), (None,)]


@pytest.mark.skipif(not os.environ.get("PG_DSN"), reason="PG_DSN not set")
def test_the_unique_index_tolerates_multiple_null_page_slugs_on_postgres(monkeypatch):
    """Same pin as above, against the real production backend. Postgres also
    treats every NULL as distinct under a UNIQUE index -- this is not a
    SQLite-only quirk the design happens to rely on."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    cx = db.connect("page_slug_null_test")
    cx.execute("DROP TABLE IF EXISTS affiliate_signups")
    cx.executescript(_PROD_DDL)
    cx.execute("ALTER TABLE affiliate_signups ADD COLUMN page_slug TEXT")
    cx.execute("CREATE UNIQUE INDEX ux_affiliate_page_slug"
               " ON affiliate_signups(page_slug)")
    for slug in ("remedy-match", "mary-boyd"):
        cx.execute(
            "INSERT INTO affiliate_signups"
            " (created_at,name,email,slug,token,status,page_slug)"
            " VALUES ('2026-01-01T00:00:00+00:00',?,?,?,?,'approved',NULL)",
            (slug.replace("-", " "), f"{slug}@example.com", slug, f"tok-{slug}"))
    cx.commit()   # must not raise
    rows = cx.execute(
        "SELECT page_slug FROM affiliate_signups ORDER BY slug").fetchall()
    assert [r[0] for r in rows] == [None, None]
    cx.close()


# ── Minting asks the WHOLE namespace, not just affiliate_signups.slug ────────
#
# The minted value is written to slug AND page_slug, and the unique index
# ux_affiliate_page_slug is on page_slug. So the moment any practitioner claims
# a vanity name, that string is occupied in the index while absent from `slug`.
# A minter that asked only `WHERE slug=?` handed back a name the INSERT then
# died on -- and because the base is deterministic from the name, every retry
# reproduced the identical collision. That signup could never complete.


def test_minting_avoids_a_name_another_practitioner_holds_as_a_page_slug(cx):
    """Glen renames his public URL to 'mary-boyd'. Mary Boyd then signs up.

    Her minted base IS 'mary-boyd', which is free in `slug` and taken in
    page_slug. Before the fix ensure_affiliate's INSERT raised UNIQUE and its
    broad except returned None -- auto-enrolment silently never completed for
    her, and /api/page-share-link answered 503 forever.
    """
    _seed(cx, slug="remedy-match")
    ps.set_page_slug(cx, "remedy-match", "mary-boyd", reserved=frozenset())

    row = affiliate_dashboard.ensure_affiliate(
        cx, "mary.boyd@example.com", name="Mary Boyd")

    assert row is not None, "the signup died on the INSERT instead of suffixing"
    assert row["slug"] != "mary-boyd"
    assert row["slug"].startswith("mary-boyd-")
    ps.check_shape(row["slug"])
    # Her own row is inside the index under its own name.
    stored = cx.execute("SELECT page_slug FROM affiliate_signups WHERE slug=?",
                        (row["slug"],)).fetchone()[0]
    assert stored == row["slug"]
    # And Glen's chosen URL is untouched -- nobody's attribution moved.
    assert cx.execute("SELECT slug, page_slug FROM affiliate_signups"
                      " WHERE email='remedy-match@example.com'").fetchone() == (
        "remedy-match", "mary-boyd")


def test_minting_avoids_a_name_published_as_an_alias(cx):
    """page_slug_is_taken reads the alias table too, so the minter now does.

    An alias is a published URL that already redirects to somebody. Minting a
    canonical slug equal to one would make resolve() stop answering for the
    practitioner the alias was published for.
    """
    _seed(cx, slug="remedy-match")
    ps.claim_alias(cx, "remedy-match", "mary-boyd", frozenset())

    row = affiliate_dashboard.ensure_affiliate(
        cx, "mary.boyd@example.com", name="Mary Boyd")

    assert row is not None
    assert row["slug"] != "mary-boyd"
    assert ps.alias_owner(cx, "mary-boyd") == "remedy-match"
