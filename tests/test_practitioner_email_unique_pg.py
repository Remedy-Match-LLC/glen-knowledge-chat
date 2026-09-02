"""The duplicate-email work against a REAL Postgres.

Everything here is SQL that a fake cursor cannot judge: whether the ORDER BY
actually beats physical row order, whether `INSERT ... SELECT ... WHERE NOT EXISTS
... RETURNING id` refuses instead of raising a type error, and whether the partial
unique index refuses to build over a violation while still allowing several scraped
directory rows to share a clinic email.

Skips when no local Postgres binary is available (CI has none). A skip here is not
a pass: tests/test_practitioner_email_unique.py covers the same behaviour against
fake cursors, and this file was run locally against PostgreSQL 16 before merge.
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

# The practitioners migrations, in dependency order.
MIGRATIONS = ("practitioners", "wallet", "practitioners-portal",
              "practitioners-phase-2", "practitioners-application",
              "practitioners-cert-finder", "practitioners-show-contact",
              "practitioner-account-linking", "practitioner-profile-fields",
              "practitioners-storefront")

# These two re-create v_practitioners_public with a narrower column list than the
# `SELECT *` before them, which Postgres rejects ("cannot drop columns from view")
# when the whole chain is applied to an empty database in one go. Production got
# there incrementally. Dropping the view first reproduces the same end state; no
# code under test reads it.
VIEW_REBUILDS = ("practitioners-cert-finder", "practitioners-show-contact")

INDEX_MIGRATION = "practitioners-portal-email-unique"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg():
    """An ephemeral Postgres cluster with the practitioners schema applied."""
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("no local postgres (initdb/pg_ctl) available")
    # Short base dir: a unix socket path over 103 bytes will not bind.
    base = tempfile.mkdtemp(dir="/tmp", prefix="pgu")
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
        cn = connect("prac")
        cn.autocommit = True
        for name in MIGRATIONS:
            if name in VIEW_REBUILDS:
                cn.cursor().execute("DROP VIEW IF EXISTS v_practitioners_public CASCADE")
            with open(os.path.join(repo, "migrations", f"{name}.sql")) as fh:
                cn.cursor().execute(fh.read())
        cn.close()
        yield {"host": base, "port": port, "connect": connect, "repo": repo}
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
        c.execute("DROP INDEX IF EXISTS ux_practitioners_portal_email")
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
    cols = ", ".join(kw)
    with conn.cursor() as c:
        c.execute(f"INSERT INTO practitioners ({cols}) "
                  f"VALUES ({', '.join(['%s'] * len(kw))}) RETURNING id",
                  tuple(kw.values()))
        return str(c.fetchone()[0])


def _role(conn, pid):
    with conn.cursor() as c:
        c.execute("SELECT portal_role FROM practitioners WHERE id=%s", (pid,))
        return c.fetchone()[0]


EMAIL = "drglenswartwout@gmail.com"


# ── the tie-break beats physical row order ────────────────────────────────────

@pytest.mark.parametrize("stub_first", [True, False])
def test_resolution_lands_on_the_real_account_either_way(db, stub_first):
    """The real defect: with two rows and no ORDER BY, the answer depended on which
    row Postgres happened to return. Inserting them in both orders is what proves
    the ordering, not luck, is doing the work."""
    from dashboard import practitioner_portal as pp
    stub = dict(name="Remedy Match", email=EMAIL, portal_role="coach",
                modules_completed=0)
    real = dict(name="Dr Glen Swartwout", email=EMAIL, portal_role="coach",
                modules_completed=12, wholesale_unlocked_at="2026-06-01T00:00:00Z",
                wallet_balance_cents=5000)
    ids = ([_insert(db, **stub), _insert(db, **real)] if stub_first
           else [_insert(db, **real), _insert(db, **stub)])
    real_id = ids[1] if stub_first else ids[0]

    assert pp.find_practitioner_id_by_email(EMAIL) == real_id
    assert pp.modules_completed_for_email(EMAIL) == 12
    assert pp.id_for_email(EMAIL) == real_id
    assert pp.name_for_email(EMAIL) == "Dr Glen Swartwout"


def test_a_portal_row_outranks_a_scraped_directory_row(db):
    """A scraped listing at the same address must never win the lookup: it has no
    session types, which is what leaves /book/<slug> showing no slots."""
    from dashboard import practitioner_portal as pp
    _insert(db, name="Scraped Listing", email=EMAIL, tier="org_member")
    portal = _insert(db, name="Dr Glen", email=EMAIL, portal_role="licensed")
    assert pp.find_practitioner_id_by_email(EMAIL) == portal
    assert pp.id_for_email(EMAIL) == portal


def test_a_writer_links_to_the_real_account_not_the_stub(db):
    """A certification promotion used to be a coin toss between the two rows."""
    from dashboard import practitioner_portal as pp
    stub = _insert(db, name="Remedy Match", email=EMAIL, portal_role="coach")
    real = _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach",
                   modules_completed=11, wholesale_unlocked_at="2026-06-01T00:00:00Z")
    pid, mc = pp.upsert_cert_student(EMAIL, modules_completed=12)
    assert pid == real and mc == 12
    with db.cursor() as c:
        c.execute("SELECT id, modules_completed FROM practitioners ORDER BY name")
        got = {str(i): m for i, m in c.fetchall()}
    assert got[real] == 12
    assert got[stub] == 0            # the stub is untouched


# ── the insert guard, on a real database ──────────────────────────────────────

@pytest.mark.parametrize("writer", ["register", "cert", "wholesale", "admin"])
def test_insert_refuses_a_second_portal_row_under_a_race(db, monkeypatch, writer):
    """The race the guard exists for: our lookup saw nothing, and by the time the
    INSERT runs another registration has already created the portal account. Forced
    here by making find_row_for_email report nothing while the row really is there,
    so the refusal has to come from the statement itself."""
    from dashboard import practitioner_admin as pa
    from dashboard import practitioner_portal as pp
    existing = _insert(db, name="Dr Glen", email=EMAIL, portal_role="licensed")
    monkeypatch.setattr(pp, "find_row_for_email", lambda *a, **k: None)

    calls = {
        "register": (pp.register_practitioner, [{
            "email": EMAIL, "name": "Impostor", "portal_role": "coach",
            "practice_name": None, "credentials": None, "phone": None, "website": None,
            "license_state": None, "license_number": None,
            "resale_license_number": "R1"}]),
        "cert": (pp.upsert_cert_student, [EMAIL]),
        "wholesale": (pp.submit_wholesale_application, [{
            "email": EMAIL, "name": "Impostor", "resale_license_number": "R1",
            "license_state": None, "practice_name": None, "credentials": None,
            "phone": None, "website": None}]),
        "admin": (pa.create_or_update_practitioner, [{
            "email": EMAIL, "name": "Impostor", "portal_role": "coach",
            "credentials": None, "wholesale_access": False, "level": 0,
            "list_in_finder": False, "city": None, "state": None, "country": "US",
            "send_invite": False}]),
    }
    fn, args = calls[writer]
    with pytest.raises(pp.DuplicatePortalEmail):
        fn(*args)
    with db.cursor() as c:
        c.execute("SELECT id FROM practitioners")
        assert [str(r[0]) for r in c.fetchall()] == [existing]


def test_the_guard_still_lets_a_first_portal_account_through(db, monkeypatch):
    """Mutation control for the test above: the same code path must succeed when no
    portal row holds the email, including when a scraped listing does."""
    from dashboard import practitioner_portal as pp
    _insert(db, name="Scraped Listing", email="new@clinic.com")
    monkeypatch.setattr(pp, "find_row_for_email", lambda *a, **k: None)
    pid, unlocked = pp.register_practitioner({
        "email": "new@clinic.com", "name": "Dr New", "portal_role": "licensed",
        "practice_name": None, "credentials": None, "phone": None, "website": None,
        "license_state": None, "license_number": "L1", "resale_license_number": None})
    assert pid and unlocked is True
    assert _role(db, pid) == "licensed"


# ── retire / unretire round trip ──────────────────────────────────────────────

def test_retire_clears_the_role_and_unretire_puts_it_back(db, tmp_path):
    from dashboard import practitioner_admin as pa
    from dashboard import practitioner_portal as pp
    real = _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach",
                   modules_completed=12, wholesale_unlocked_at="2026-06-01T00:00:00Z")
    stub = _insert(db, name="Remedy Match", email=EMAIL, portal_role="coach")

    out = pa.retire_practitioner(stub, db_path=str(tmp_path / "chat_log.db"))
    assert out["retired_role"] == "coach"
    assert _role(db, stub) is None
    assert _role(db, real) == "coach"
    with db.cursor() as c:                       # the record itself survives
        c.execute("SELECT name FROM practitioners WHERE id=%s", (stub,))
        assert c.fetchone()[0] == "Remedy Match"

    # Reversible, but not back into a duplicate while the real account holds the email.
    with pytest.raises(pp.DuplicatePortalEmail):
        pa.unretire_practitioner(stub, "coach")
    with db.cursor() as c:      # the real account moves off the email
        c.execute("UPDATE practitioners SET portal_role=NULL WHERE id=%s", (real,))
    assert pa.unretire_practitioner(stub, "coach")["portal_role"] == "coach"
    assert _role(db, stub) == "coach"


def test_retire_refuses_the_account_that_is_in_use(db, tmp_path):
    from dashboard import practitioner_admin as pa
    real = _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach",
                   modules_completed=12, wholesale_unlocked_at="2026-06-01T00:00:00Z",
                   wallet_balance_cents=2500)
    with pytest.raises(pa.RetireBlocked) as e:
        pa.retire_practitioner(real, db_path=str(tmp_path / "chat_log.db"))
    assert "wholesale" in str(e.value) and "level 12" in str(e.value)
    assert _role(db, real) == "coach"


# ── the audit sees the whole table ────────────────────────────────────────────

def test_audit_reports_both_the_stub_pair_and_a_shared_clinic_email(db, tmp_path):
    from dashboard import practitioner_admin as pa
    _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach", modules_completed=12,
            wholesale_unlocked_at="2026-06-01T00:00:00Z")
    _insert(db, name="Remedy Match", email=EMAIL, portal_role="coach")
    _insert(db, name="Aide One", email="front@clinic.com")
    _insert(db, name="Aide Two", email="front@clinic.com")
    _insert(db, name="Solo", email="solo@x.com", portal_role="licensed")

    report = pa.audit_duplicate_emails(db_path=str(tmp_path / "chat_log.db"))
    assert report["emails"] == 2
    assert report["rows"] == 4
    assert report["portal_conflicts"] == 1        # only the stub pair
    glen = [g for g in report["groups"] if g["email"] == EMAIL][0]
    assert glen["portal_count"] == 2
    assert sorted(r["name"] for r in glen["rows"]) == ["Dr Glen", "Remedy Match"]
    assert [r["level"] for r in glen["rows"] if r["name"] == "Dr Glen"] == [12]
    clinic = [g for g in report["groups"] if g["email"] == "front@clinic.com"][0]
    assert clinic["portal_count"] == 0


# ── the database backstop ─────────────────────────────────────────────────────

def test_index_is_blocked_while_a_duplicate_exists_and_says_so(db, capsys):
    from dashboard import practitioner_admin as pa
    _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach")
    _insert(db, name="Remedy Match", email=EMAIL.upper(), portal_role="coach")
    out = pa.ensure_portal_email_unique_index()
    assert out == {"index": "ux_practitioners_portal_email", "created": False,
                   "present": False, "blocked_by": 1}
    assert "NOT created" in capsys.readouterr().out
    assert pa.portal_email_index_present() is False


def test_index_is_created_over_a_clean_table_and_then_holds(db):
    from dashboard import practitioner_admin as pa
    _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach")
    _insert(db, name="Aide One", email="front@clinic.com")
    _insert(db, name="Aide Two", email="front@clinic.com")   # scraped, must stay legal
    out = pa.ensure_portal_email_unique_index()
    assert out["created"] is True and out["present"] is True
    assert pa.portal_email_index_present() is True

    # Case-insensitive, and it is the database refusing now, not the writer.
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(db, name="Impostor", email=EMAIL.upper(), portal_role="coach")

    # A third practitioner at the shared clinic address is still fine.
    assert _insert(db, name="Aide Three", email="front@clinic.com")


def test_the_migration_file_refuses_loudly_over_a_violation(db, pg):
    """The migration is applied by hand with psql. It must raise rather than
    half-apply, or an operator reads a quiet run as an enforced database."""
    sql = open(os.path.join(pg["repo"], "migrations",
                            f"{INDEX_MIGRATION}.sql")).read()
    _insert(db, name="Dr Glen", email=EMAIL, portal_role="coach")
    _insert(db, name="Remedy Match", email=EMAIL, portal_role="coach")
    with db.cursor() as c:
        with pytest.raises(psycopg2.errors.RaiseException) as e:
            c.execute(sql)
    assert "not created" in str(e.value)
    with db.cursor() as c:
        c.execute("DELETE FROM practitioners WHERE name='Remedy Match'")
        c.execute(sql)
    from dashboard import practitioner_admin as pa
    assert pa.portal_email_index_present() is True
