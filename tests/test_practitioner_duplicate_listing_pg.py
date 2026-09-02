"""The duplicate-listing migration against a REAL Postgres.

migrations/practitioners-duplicate-listing.sql recreates v_practitioners_public.
The finder reads that view. A recreation that quietly dropped a column, or
reordered one, would take the public directory down, and no fake cursor can
judge that: only a real `CREATE OR REPLACE VIEW` against a real catalogue can.

So the fixture applies the whole practitioners chain UP TO the current
production view, records the view's exact column list from the catalogue, then
applies the new migration and compares. Same columns, same order, same types, or
this file fails.

Skips when no local Postgres binary is available (CI has none). A skip here is
not a pass: tests/test_practitioner_duplicate_listing.py covers the Python side
against fake cursors, and this file was run locally against the Homebrew
PostgreSQL before merge.
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

# The practitioners migrations, in dependency order. practitioners-farms is the
# most recent recreation of v_practitioners_public, so it is what production's
# view actually looks like; the new migration must be a careful edit of THAT.
MIGRATIONS = ("practitioners", "wallet", "practitioners-portal",
              "practitioners-phase-2", "practitioners-application",
              "practitioners-cert-finder", "practitioners-show-contact",
              "practitioner-account-linking", "practitioner-profile-fields",
              "practitioners-storefront", "practitioners-farms")

# These two re-create the view with a narrower column list than the `SELECT *`
# before them, which Postgres rejects ("cannot drop columns from view") when the
# whole chain is applied to an empty database in one go. Production got there
# incrementally. Dropping the view first reproduces the same end state.
VIEW_REBUILDS = ("practitioners-cert-finder", "practitioners-show-contact")

MIGRATION = "practitioners-duplicate-listing"

VIEW = "v_practitioners_public"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _view_columns(conn):
    """The view's columns as the catalogue sees them: name, position, type."""
    with conn.cursor() as c:
        c.execute(
            "SELECT column_name, ordinal_position, data_type FROM information_schema.columns "
            "WHERE table_name=%s ORDER BY ordinal_position", (VIEW,))
        return [tuple(r) for r in c.fetchall()]


@pytest.fixture(scope="module")
def pg():
    """An ephemeral Postgres with the practitioners schema at its CURRENT state,
    plus the view's column list recorded BEFORE the new migration is applied."""
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("no local postgres (initdb/pg_ctl) available")
    base = tempfile.mkdtemp(dir="/tmp", prefix="pgd")
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

    try:
        cn = connect()
        cn.autocommit = True
        cn.cursor().execute("CREATE DATABASE prac")
        cn.close()
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def apply(cn, name):
            with open(os.path.join(repo, "migrations", f"{name}.sql")) as fh:
                cn.cursor().execute(fh.read())

        cn = connect("prac")
        cn.autocommit = True
        for name in MIGRATIONS:
            if name in VIEW_REBUILDS:
                cn.cursor().execute(f"DROP VIEW IF EXISTS {VIEW} CASCADE")
            apply(cn, name)
        # The baseline: what the finder reads TODAY.
        before = _view_columns(cn)
        assert before, "the schema chain did not create the view at all"
        apply(cn, MIGRATION)
        after = _view_columns(cn)
        with cn.cursor() as c:
            c.execute("SELECT version()")
            version = c.fetchone()[0]
        cn.close()
        yield {"host": base, "port": port, "connect": connect, "repo": repo,
               "before": before, "after": after, "version": version}
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def db(pg, monkeypatch):
    """A clean practitioners table, with db_supabase pointed at it."""
    conn = pg["connect"]("prac")
    conn.autocommit = True
    with conn.cursor() as c:
        c.execute("TRUNCATE practitioners CASCADE")

    @contextmanager
    def _cursor():
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()

    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", _cursor)
    try:
        yield conn
    finally:
        conn.close()


def _insert(conn, **kw):
    kw.setdefault("tier", "org_member")
    kw.setdefault("name", "A Practitioner")
    cols = ", ".join(kw)
    with conn.cursor() as c:
        c.execute(f"INSERT INTO practitioners ({cols}) "
                  f"VALUES ({', '.join(['%s'] * len(kw))}) RETURNING id",
                  tuple(kw.values()))
        return str(c.fetchone()[0])


def _public_ids(conn):
    with conn.cursor() as c:
        c.execute(f"SELECT id FROM {VIEW}")
        return {str(r[0]) for r in c.fetchall()}


EMAIL = "front@clinic.com"


# ── this really is a database ─────────────────────────────────────────────────

def test_it_is_a_real_postgres_server(pg, capsys):
    """The fixture starts in well under a second on a fast machine, which looks
    exactly like a fixture that quietly did nothing. Naming the server it talked
    to is what separates the two, and it prints so a run can be read back."""
    with capsys.disabled():
        print(f"\n[duplicate-listing-pg] {pg['version']}")
    assert pg["version"].startswith("PostgreSQL ")


# ── the recreated view is faithful to the one it replaced ─────────────────────

def test_the_recreated_view_has_exactly_the_same_columns(pg):
    """The whole risk of this migration in one assertion. A `CREATE OR REPLACE
    VIEW` that dropped or reordered a column would break the finder; Postgres
    would happily accept a version that ADDED one, which would newly expose it
    on the public search API."""
    assert pg["after"] == pg["before"]


def test_the_view_still_carries_the_columns_the_finder_reads(pg):
    """A guard against both lists being empty or both being wrong together."""
    names = [c[0] for c in pg["after"]]
    assert len(names) == 36
    for expected in ("id", "tier", "name", "lat", "lng", "specialties", "email",
                     "phone", "show_contact", "removal_requested", "products",
                     "order_options", "modules_completed", "accepts_inquiries"):
        assert expected in names


def test_duplicate_of_is_not_exposed_on_the_public_view(pg):
    """Every row the view returns has it NULL by construction, so publishing it
    would add a column of no information to an unauthenticated payload."""
    assert "duplicate_of" not in [c[0] for c in pg["after"]]


def test_the_migration_is_rerunnable(pg):
    """It is applied by hand. Running it twice must not fail or change anything."""
    cn = pg["connect"]("prac")
    cn.autocommit = True
    try:
        with open(os.path.join(pg["repo"], "migrations", f"{MIGRATION}.sql")) as fh:
            cn.cursor().execute(fh.read())
        assert _view_columns(cn) == pg["before"]
    finally:
        cn.close()


# ── a marked row leaves the finder, and comes back ────────────────────────────

def test_a_marked_row_disappears_from_the_view_and_keeps_its_coordinates(db):
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    assert _public_ids(db) == {keep, dup}

    from dashboard import practitioner_admin as pa
    assert pa.mark_duplicate_of(dup, keep)["duplicate_of"] == keep
    assert _public_ids(db) == {keep}

    with db.cursor() as c:      # nothing was taken away from the hidden row
        c.execute("SELECT lat, lng, removal_requested, email FROM practitioners "
                  "WHERE id=%s", (dup,))
        lat, lng, removed, email = c.fetchone()
    assert lat is not None and lng is not None
    assert removed is False              # the opt-out flag is untouched
    assert email == EMAIL


def test_the_undo_puts_it_back_in_the_view(db):
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    from dashboard import practitioner_admin as pa
    pa.mark_duplicate_of(dup, keep)
    assert _public_ids(db) == {keep}
    assert pa.unmark_duplicate(dup)["was_duplicate_of"] == keep
    assert _public_ids(db) == {keep, dup}


def test_the_other_two_filters_still_work(db):
    """Control for the test above: the new AND must not be the only thing left."""
    listed = _insert(db, name="Listed", email=EMAIL, lat=21.3, lng=-157.8)
    _insert(db, name="Opted out", email=EMAIL, lat=21.3, lng=-157.8,
            removal_requested=True)
    _insert(db, name="Ungeocoded", email=EMAIL)
    assert _public_ids(db) == {listed}


# ── the refusals, against a real database ─────────────────────────────────────

def test_a_portal_account_is_refused_and_stays_in_the_finder(db):
    """A portal account is somebody's login. Hiding it this way is never allowed."""
    from dashboard import practitioner_admin as pa
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    account = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8,
                      portal_role="licensed")
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of(account, keep)
    assert "portal account" in str(e.value)
    assert _public_ids(db) == {keep, account}


def test_two_different_emails_are_refused(db):
    from dashboard import practitioner_admin as pa
    keep = _insert(db, name="Dr One", email="one@clinic.com", lat=21.3, lng=-157.8)
    other = _insert(db, name="Dr Two", email="two@clinic.com", lat=21.3, lng=-157.8)
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of(other, keep)
    assert "do not share an email" in str(e.value)
    assert _public_ids(db) == {keep, other}


def test_an_unknown_target_is_refused(db):
    from dashboard import practitioner_admin as pa
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    ghost = "00000000-0000-0000-0000-0000000000ff"
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of(dup, ghost)
    assert "no practitioner with that id" in str(e.value)
    assert _public_ids(db) == {dup}


def test_a_chain_is_refused_from_both_ends(db):
    from dashboard import practitioner_admin as pa
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    mid = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    third = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    pa.mark_duplicate_of(mid, keep)

    with pytest.raises(pa.DuplicateMarkBlocked) as e:   # survivor is hidden
        pa.mark_duplicate_of(third, mid)
    assert "itself marked as a duplicate" in str(e.value)
    with pytest.raises(pa.DuplicateMarkBlocked) as e:   # survivor has dependents
        pa.mark_duplicate_of(keep, third)
    assert "hide them too" in str(e.value)
    assert _public_ids(db) == {keep, third}


# ── the database's own backstops ──────────────────────────────────────────────

def test_the_database_refuses_a_self_reference_and_a_ghost_survivor(db):
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    with db.cursor() as c:
        with pytest.raises(psycopg2.errors.CheckViolation):
            c.execute("UPDATE practitioners SET duplicate_of=id WHERE id=%s", (dup,))
    with db.cursor() as c:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            c.execute("UPDATE practitioners SET duplicate_of=%s WHERE id=%s",
                      ("00000000-0000-0000-0000-0000000000ff", dup))


def test_deleting_the_survivor_restores_the_listing_rather_than_deleting_it(db):
    """ON DELETE SET NULL, not CASCADE: removing the row we kept must never take
    the listing that was folded into it with it."""
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    from dashboard import practitioner_admin as pa
    pa.mark_duplicate_of(dup, keep)
    with db.cursor() as c:
        c.execute("DELETE FROM practitioners WHERE id=%s", (keep,))
    assert _public_ids(db) == {dup}


# ── the audit, reading the real column ────────────────────────────────────────

def test_the_audit_finder_duplicate_count_falls_when_a_row_is_marked(db, tmp_path):
    """The number the exercise is measured by, computed from the real table."""
    from dashboard import practitioner_admin as pa
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    log = str(tmp_path / "chat_log.db")

    before = pa.audit_duplicate_emails(db_path=log)
    assert before["finder_duplicates"] == 1
    assert before["groups"][0]["finder_listed_count"] == 2

    pa.mark_duplicate_of(dup, keep)
    after = pa.audit_duplicate_emails(db_path=log)
    assert after["finder_duplicates"] == 0
    assert after["groups"][0]["finder_listed_count"] == 1
    assert after["groups"][0]["count"] == 2          # both rows still reported
    by = {r["id"]: r for r in after["groups"][0]["rows"]}
    assert by[dup]["duplicate_of"] == keep
    assert by[dup]["finder_listed"] is False
    assert by[dup]["has_coords"] is True
    assert by[keep]["duplicate_of"] is None


def test_the_audit_agrees_with_the_view(db, tmp_path):
    """The audit computes finder_listed in Python; the view does it in SQL. They
    are two implementations of one rule, so they must not be able to disagree."""
    from dashboard import practitioner_admin as pa
    keep = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    dup = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8)
    opted = _insert(db, name="Dr Real", email=EMAIL, lat=21.3, lng=-157.8,
                    removal_requested=True)
    thin = _insert(db, name="Dr Real", email=EMAIL)
    pa.mark_duplicate_of(dup, keep)

    report = pa.audit_duplicate_emails(db_path=str(tmp_path / "chat_log.db"))
    audited = {r["id"] for g in report["groups"] for r in g["rows"] if r["finder_listed"]}
    assert audited == _public_ids(db) == {keep}
    assert {dup, opted, thin}.isdisjoint(audited)
