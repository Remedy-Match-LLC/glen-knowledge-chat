"""Unit tests for the practitioner slug namespace. Imports no Flask app."""
import sqlite3

import pytest
from werkzeug.routing import Map, Rule

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
