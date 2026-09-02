"""apply_duplicate_listing_migration against a REAL Postgres.

The console endpoint applies migrations/practitioners-duplicate-listing.sql by
hand, from inside Render, because production is unreachable from a laptop with
psql. It has to be idempotent, it has to apply the DO $$ blocks whole rather
than split on semicolons, and its post-apply verification has to actually read
the catalogue rather than trust "no exception was raised" — a CREATE OR REPLACE
VIEW that silently dropped a column returns with no error, and
v_practitioners_public is what the public finder reads.

Each test gets its OWN database, created fresh from the practitioners chain up
to (but not including) practitioners-duplicate-listing, so both the "column
absent" and "column present" states are real rather than asserted against a
fake cursor.

Skips when no local Postgres binary is available (CI has none), same as its
siblings tests/test_practitioner_email_unique_pg.py and
tests/test_practitioner_duplicate_listing_pg.py.
"""
import os
import shutil
import socket
import subprocess
import tempfile
from contextlib import contextmanager

import pytest

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

# The practitioners migrations, in dependency order, up to the CURRENT production
# view (practitioners-farms). Deliberately NOT including practitioners-duplicate-
# listing — that is the migration under test, applied inside each test itself.
MIGRATIONS = ("practitioners", "wallet", "practitioners-portal",
              "practitioners-phase-2", "practitioners-application",
              "practitioners-cert-finder", "practitioners-show-contact",
              "practitioner-account-linking", "practitioner-profile-fields",
              "practitioners-storefront", "practitioners-farms")

VIEW_REBUILDS = ("practitioners-cert-finder", "practitioners-show-contact")

MIGRATION = "practitioners-duplicate-listing"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg():
    """One ephemeral Postgres SERVER for the whole module. Each test creates and
    drops its own database on it, so schema mutations in one test never leak
    into another."""
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("no local postgres (initdb/pg_ctl) available")
    base = tempfile.mkdtemp(dir="/tmp", prefix="pga")
    data = os.path.join(base, "d")
    port = _free_port()
    try:
        subprocess.run([initdb, "-D", data, "-U", "postgres", "-A", "trust"],
                       check=True, capture_output=True)
        subprocess.run([pg_ctl, "-D", data, "-w", "-l", os.path.join(base, "log"),
                        "-o", f"-p {port} -k {base} -c listen_addresses=''", "start"],
                       check=True, capture_output=True, timeout=60)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"could not start a local postgres: {e}")

    def connect(dbname="postgres"):
        return psycopg2.connect(host=base, port=port, user="postgres", dbname=dbname)

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        yield {"connect": connect, "repo": repo}
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


_counter = [0]


@pytest.fixture
def db(pg, monkeypatch):
    """A fresh database, schema applied up to (not including) the migration under
    test, with db_supabase pointed at it. Dropped in teardown."""
    _counter[0] += 1
    dbname = f"prac_apply_{_counter[0]}"

    admin = pg["connect"]()
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {dbname}")
    admin.close()

    conn = pg["connect"](dbname)
    conn.autocommit = True
    for name in MIGRATIONS:
        if name in VIEW_REBUILDS:
            conn.cursor().execute("DROP VIEW IF EXISTS v_practitioners_public CASCADE")
        with open(os.path.join(pg["repo"], "migrations", f"{name}.sql")) as fh:
            conn.cursor().execute(fh.read())

    # A SEPARATE connection for the monkeypatched supabase_cursor, with autocommit
    # OFF — mirroring db_supabase.supabase_cursor's real contract exactly: commit
    # only if the `with` block exits clean, rollback (undoing the DDL too) on any
    # exception. Reusing `conn` (autocommit=True, for setup/assertions below)
    # would hide that contract entirely: an autocommit statement commits itself
    # the instant it runs, so a later verification failure would have nothing
    # left to roll back — exactly the gap the rollback test below needs to be
    # real, not accidental.
    txn_conn = pg["connect"](dbname)

    @contextmanager
    def _cursor():
        cur = txn_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            txn_conn.commit()
        except Exception:
            txn_conn.rollback()
            raise
        finally:
            cur.close()

    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", _cursor)
    try:
        yield conn
    finally:
        conn.close()
        txn_conn.close()
        admin = pg["connect"]()
        admin.autocommit = True
        admin.cursor().execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{dbname}' AND pid <> pg_backend_pid()")
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {dbname}")
        admin.close()


def _column_exists(conn, table, column):
    with conn.cursor() as c:
        c.execute("SELECT 1 FROM information_schema.columns "
                  "WHERE table_name=%s AND column_name=%s", (table, column))
        return c.fetchone() is not None


def _view_columns(conn, view="v_practitioners_public"):
    with conn.cursor() as c:
        c.execute("SELECT column_name FROM information_schema.columns "
                  "WHERE table_name=%s ORDER BY ordinal_position", (view,))
        return [r[0] for r in c.fetchall()]


def _insert(conn, **kw):
    kw.setdefault("tier", "org_member")
    kw.setdefault("name", "A Practitioner")
    cols = ", ".join(kw)
    with conn.cursor() as c:
        c.execute(f"INSERT INTO practitioners ({cols}) "
                  f"VALUES ({', '.join(['%s'] * len(kw))}) RETURNING id",
                  tuple(kw.values()))
        return str(c.fetchone()[0])


# ── the presence flag reads the catalogue ──────────────────────────────────────

def test_column_absent_before_the_migration(db):
    from dashboard import practitioner_admin as pa
    assert _column_exists(db, "practitioners", "duplicate_of") is False
    assert pa.duplicate_of_column_present() is False


def test_column_present_after_the_migration(db):
    from dashboard import practitioner_admin as pa
    pa.apply_duplicate_listing_migration()
    assert _column_exists(db, "practitioners", "duplicate_of") is True
    assert pa.duplicate_of_column_present() is True


# ── applying it ─────────────────────────────────────────────────────────────────

def test_apply_reports_success_and_the_column_and_view(db):
    from dashboard import practitioner_admin as pa
    before_view = _view_columns(db)
    assert before_view, "the base chain did not create the view at all"

    out = pa.apply_duplicate_listing_migration()
    assert out["applied"] is True
    assert out["duplicate_of_present"] is True
    assert out["view_columns"] == before_view   # same columns, same order
    assert "duplicate_of" not in out["view_columns"]


def test_apply_is_idempotent(db):
    """Calling it twice must not raise and must leave the same state — the
    console operator can safely retry."""
    from dashboard import practitioner_admin as pa
    first = pa.apply_duplicate_listing_migration()
    second = pa.apply_duplicate_listing_migration()
    assert first == second
    assert pa.duplicate_of_column_present() is True
    assert _view_columns(db) == first["view_columns"]


def test_apply_reads_the_whole_file_in_one_statement(db, monkeypatch):
    """The migration's DO $$ blocks would be broken by a naive split on ';'. This
    confirms the file text handed to the cursor still contains the dollar-quoted
    body whole, which is the only way psycopg2/Postgres parses it correctly."""
    from dashboard import practitioner_admin as pa
    seen = []
    real_open = open

    def _spy(path, *a, **kw):
        fh = real_open(path, *a, **kw)
        if path.endswith(f"{pa.DUPLICATE_LISTING_MIGRATION}.sql"):
            seen.append(fh.read())
            fh.seek(0)
        return fh

    monkeypatch.setattr("builtins.open", _spy)
    pa.apply_duplicate_listing_migration()
    assert len(seen) == 1
    assert "DO $$" in seen[0] and "END$$;" in seen[0]


# ── the view stays whole, and a duplicate row is excluded ──────────────────────

def test_view_keeps_every_prior_column_and_excludes_a_marked_row(db):
    from dashboard import practitioner_admin as pa
    before_view = set(_view_columns(db))

    keep = _insert(db, name="Dr Real", email="front@clinic.com", lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email="front@clinic.com", lat=21.3, lng=-157.8)

    pa.apply_duplicate_listing_migration()
    after_view = set(_view_columns(db))
    assert after_view == before_view      # every prior column survives, none added

    with db.cursor() as c:
        c.execute("UPDATE practitioners SET duplicate_of=%s WHERE id=%s", (keep, dup))
    with db.cursor() as c:
        c.execute("SELECT id FROM v_practitioners_public")
        ids = {str(r[0]) for r in c.fetchall()}
    assert ids == {keep}
    assert dup not in ids


# ── it fails loudly instead of reporting success on a partial apply ────────────

# A stand-in for a broken recreation. Postgres itself refuses a CREATE OR REPLACE
# VIEW that drops or reorders an existing column ("cannot drop columns from
# view") — that failure mode is already caught by the database before this
# function's own verification ever runs. What Postgres DOES accept silently is a
# trailing column APPENDED to the view, which is exactly the risk the migration's
# own comment names: "a SELECT * refresh would newly expose sensitive columns
# added since (wallet_balance_cents, ...) through the PUBLIC search API." This
# reproduces that: duplicate_of lands correctly, but the view grows an extra,
# unintended column.
_BROKEN_MIGRATION_SQL = """
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS duplicate_of uuid;
CREATE OR REPLACE VIEW v_practitioners_public
WITH (security_invoker = on) AS
SELECT id, tier, source_org, source_url, fellowship_level, specialties, name,
       practice_name, credentials, phone, email, website, address1, city, state,
       postal, country, lat, lng, geocode_quality, photo_url, bio,
       accepting_new_patients, telehealth, ghl_contact_id, removal_requested,
       last_scraped_at, created_at, updated_at, accepts_inquiries,
       claim_token_hash, claim_verified_at, modules_completed, show_contact,
       products, order_options, wallet_balance_cents
FROM practitioners
WHERE removal_requested = false AND lat IS NOT NULL AND duplicate_of IS NULL;
"""


def test_a_view_that_gained_an_unintended_column_is_caught_and_rolled_back(
        db, monkeypatch):
    """The real risk the task calls out: v_practitioners_public feeds the public
    finder, and a recreation that quietly changes its shape must not read as a
    clean apply. Postgres allows a CREATE OR REPLACE VIEW to append a trailing
    column without complaint (only dropping/reordering existing ones is
    refused), so this is the failure mode that has to be caught by actually
    reading the catalogue, not by trusting that no exception was raised. This
    points the apply function at exactly that broken recreation (same file
    name, redirected contents) and asserts the real verification code in
    dashboard.practitioner_admin.apply_duplicate_listing_migration catches it,
    names the extra column, and rolls the whole apply back — the column add
    that DID succeed must not survive either, or a later retry would read
    `duplicate_of_present: True` while the view stayed wrong."""
    from dashboard import practitioner_admin as pa
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
    tmp.write(_BROKEN_MIGRATION_SQL)
    tmp.close()
    real_open = open

    def _redirect(path, *a, **kw):
        if path.endswith(f"{pa.DUPLICATE_LISTING_MIGRATION}.sql"):
            path = tmp.name
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _redirect)
    try:
        with pytest.raises(RuntimeError) as e:
            pa.apply_duplicate_listing_migration()
        assert "wallet_balance_cents" in str(e.value)
    finally:
        os.unlink(tmp.name)

    # Rolled back: even the column add (which DID succeed on its own) is gone,
    # because it ran in the same transaction as the failed verification.
    assert _column_exists(db, "practitioners", "duplicate_of") is False
    assert pa.duplicate_of_column_present() is False
