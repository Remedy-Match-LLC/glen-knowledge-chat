"""One portal account per email: retire, duplicate audit, and the write guards.

`practitioners` holds two different populations in one table: the scraped
practitioner directory (several rows may legitimately share a clinic email) and
portal accounts (`portal_role` set). Duplicates across those two are how
drglenswartwout@gmail.com ended up with a real level-12 account AND an empty
"Remedy Match" stub, with every writer resolving by an unordered
`lower(email)=lower(%s) LIMIT 1` and landing on whichever row Postgres felt like
returning.

These tests cover the pure and fake-cursor surface. The SQL itself (the
INSERT ... WHERE NOT EXISTS guard, the ORDER BY tie-break, and the partial unique
index) is exercised against a real Postgres in
tests/test_practitioner_email_unique_pg.py.
"""
import pytest


# ── fake Supabase cursor (modeled on tests/test_cert_student.py) ──────────────

class _FakeCur:
    """Records every statement; serves fetchone() from a queue."""

    def __init__(self, fetchone_queue=None, fetchall_result=None):
        self.executed = []                       # [(sql, params)]
        self._one = list(fetchone_queue or [])
        self._all = fetchall_result if fetchall_result is not None else []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), list(params or ())))

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return self._all

    def sql(self):
        return [s for s, _ in self.executed]


class _FakeCtx:
    def __init__(self, cur): self.cur = cur
    def __enter__(self): return self.cur
    def __exit__(self, *a): return False


def _patch_cursor(monkeypatch, cur):
    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(cur))
    return cur


# ── the deterministic tie-break, on every writer's email lookup ───────────────

def test_every_email_resolution_is_ordered(monkeypatch):
    """No writer may resolve an email with a bare LIMIT 1.

    With two rows for one address an unordered LIMIT 1 is a coin toss, which is
    how a certification promotion could have landed on the empty stub instead of
    the real account. Each of these five statements is what actually reaches the
    database, so this asserts on the SQL the cursor is handed, not on source text.
    """
    from dashboard import practitioner_admin as pa
    from dashboard import practitioner_portal as pp

    def _lookups(fn, *a, **kw):
        cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[{"id": "x", "tier": None,
                                                                  "wholesale_unlocked_at": None}]))
        try:
            fn(*a, **kw)
        except Exception:
            pass
        return [s for s in cur.sql()
                if s.startswith("SELECT") and "FROM practitioners WHERE lower(email)" in s]

    clean_reg = {"email": "a@b.com", "name": "A", "portal_role": "coach",
                 "practice_name": None, "credentials": None, "phone": None,
                 "website": None, "license_state": None, "license_number": None,
                 "resale_license_number": "R1"}
    clean_ws = {"email": "a@b.com", "name": "A", "resale_license_number": "R1",
                "license_state": None, "practice_name": None, "credentials": None,
                "phone": None, "website": None}
    clean_admin = {"email": "a@b.com", "name": "A", "portal_role": "coach",
                   "credentials": None, "wholesale_access": False, "level": 0,
                   "list_in_finder": False, "city": None, "state": None,
                   "country": "US", "send_invite": False}

    seen = []
    seen += _lookups(pp.find_practitioner_id_by_email, "a@b.com")
    seen += _lookups(pp.register_practitioner, clean_reg)
    seen += _lookups(pp.upsert_cert_student, "a@b.com")
    seen += _lookups(pp.submit_wholesale_application, clean_ws)
    seen += _lookups(pa.create_or_update_practitioner, clean_admin)

    assert len(seen) == 5, seen
    for s in seen:
        assert "ORDER BY" in s, s
        assert pp.EMAIL_PICK_ORDER.split("LIMIT")[0].strip() in s, s


def test_the_tie_break_prefers_the_row_that_is_actually_in_use():
    """The ordering has to name the columns that distinguish a real account from a
    stub, portal_role first: a portal row always outranks a scraped directory row."""
    from dashboard import practitioner_portal as pp
    order = pp.EMAIL_PICK_ORDER
    assert order.index("portal_role") < order.index("wholesale_unlocked_at")
    assert order.index("wholesale_unlocked_at") < order.index("modules_completed")
    assert order.rstrip().endswith("LIMIT 1")


# ── insert guard: no second portal row for an email ───────────────────────────

@pytest.mark.parametrize("call", ["register", "cert", "wholesale", "admin"])
def test_insert_refuses_when_a_portal_row_already_holds_the_email(monkeypatch, call):
    """Every INSERT carries its own NOT EXISTS guard, so a lookup that saw nothing
    (a stale snapshot, or a concurrent registration) cannot mint a second portal
    account. The cursor returning no row from `RETURNING id` IS the refusal."""
    from dashboard import practitioner_admin as pa
    from dashboard import practitioner_portal as pp

    # fetchone queue: the email lookup finds nothing, the guarded INSERT returns nothing.
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None, None]))
    args = {
        "register": (pp.register_practitioner,
                     ({"email": "a@b.com", "name": "A", "portal_role": "coach",
                       "practice_name": None, "credentials": None, "phone": None,
                       "website": None, "license_state": None, "license_number": None,
                       "resale_license_number": "R1"},)),
        "cert": (pp.upsert_cert_student, ("a@b.com",)),
        "wholesale": (pp.submit_wholesale_application,
                      ({"email": "a@b.com", "name": "A", "resale_license_number": "R1",
                        "license_state": None, "practice_name": None,
                        "credentials": None, "phone": None, "website": None},)),
        "admin": (pa.create_or_update_practitioner,
                  ({"email": "a@b.com", "name": "A", "portal_role": "coach",
                    "credentials": None, "wholesale_access": False, "level": 0,
                    "list_in_finder": False, "city": None, "state": None,
                    "country": "US", "send_invite": False},)),
    }[call]
    fn, fnargs = args
    with pytest.raises(pp.DuplicatePortalEmail) as e:
        fn(*fnargs)
    assert "a@b.com" in str(e.value)

    inserts = [s for s in cur.sql() if "INSERT INTO practitioners" in s]
    assert len(inserts) == 1, cur.sql()
    assert "WHERE NOT EXISTS" in inserts[0], inserts[0]
    assert "portal_role IS NOT NULL" in inserts[0], inserts[0]


# ── retire: the refusal ───────────────────────────────────────────────────────

def test_retire_blockers_names_every_attachment():
    from dashboard import practitioner_admin as pa
    reasons = pa.retire_blockers(
        {"modules_completed": 12, "wallet_balance_cents": 2500,
         "wholesale_unlocked_at": "2026-06-01T00:00:00"},
        {"orders": 2, "disp_count": 1})
    joined = " ".join(reasons).lower()
    assert "order" in joined
    assert "wholesale" in joined
    assert "level 12" in joined
    assert "25.00" in joined
    assert "--" not in joined and "—" not in joined


def test_retire_blockers_empty_for_a_bare_stub():
    from dashboard import practitioner_admin as pa
    assert pa.retire_blockers(
        {"modules_completed": 0, "wallet_balance_cents": 0,
         "wholesale_unlocked_at": None}, {}) == []


def test_retire_refuses_a_row_with_orders(monkeypatch, tmp_path):
    """Retiring an account somebody is actually using is the damage worth
    preventing; the operator gets a readable refusal, not a silent no-op."""
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        {"id": "dc119bc6", "name": "Dr Glen", "email": "g@x.com", "portal_role": "coach",
         "modules_completed": 12, "wallet_balance_cents": 0,
         "wholesale_unlocked_at": "2026-06-01T00:00:00"}]))
    monkeypatch.setattr(pa, "aggregate_activity", lambda p: {"dc119bc6": {"orders": 2}})
    with pytest.raises(pa.RetireBlocked) as e:
        pa.retire_practitioner("dc119bc6", db_path=str(tmp_path / "x.db"))
    msg = str(e.value)
    assert "Dr Glen" in msg and "order" in msg and "level 12" in msg


def test_retire_clears_the_role_and_never_deletes(monkeypatch, tmp_path):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        {"id": "3c090c94", "name": "Remedy Match", "email": "g@x.com",
         "portal_role": "coach", "modules_completed": 0, "wallet_balance_cents": 0,
         "wholesale_unlocked_at": None}]))
    monkeypatch.setattr(pa, "aggregate_activity", lambda p: {})
    out = pa.retire_practitioner("3c090c94", db_path=str(tmp_path / "x.db"))
    assert out["retired_role"] == "coach"
    assert out["email"] == "g@x.com"
    updates = [s for s in cur.sql() if s.startswith("UPDATE practitioners")]
    assert updates and "portal_role=NULL" in updates[0]
    assert not any("DELETE" in s.upper() for s in cur.sql())


def test_retire_unknown_row(monkeypatch, tmp_path):
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    with pytest.raises(pa.PractitionerNotFound):
        pa.retire_practitioner("nope", db_path=str(tmp_path / "x.db"))


# ── retire is reversible ──────────────────────────────────────────────────────

def test_unretire_puts_the_role_back(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        {"id": "3c090c94", "name": "Remedy Match", "email": "g@x.com",
         "portal_role": None},
        None,                       # no other portal row holds the email
    ]))
    out = pa.unretire_practitioner("3c090c94", "coach")
    assert out["portal_role"] == "coach"
    updates = [s for s in cur.sql() if s.startswith("UPDATE practitioners")]
    assert updates and "portal_role=%s" in updates[0]


def test_unretire_refuses_to_recreate_the_duplicate(monkeypatch):
    """Restoring a role must not put back the second portal account it removed."""
    from dashboard import practitioner_admin as pa
    from dashboard import practitioner_portal as pp
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        {"id": "3c090c94", "name": "Remedy Match", "email": "g@x.com",
         "portal_role": None},
        {"id": "dc119bc6", "name": "Dr Glen"},   # the real account still holds it
    ]))
    with pytest.raises(pp.DuplicatePortalEmail):
        pa.unretire_practitioner("3c090c94", "coach")


def test_unretire_rejects_an_unknown_role(monkeypatch):
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur())
    with pytest.raises(ValueError):
        pa.unretire_practitioner("p1", "wizard")


# ── duplicate audit (pure grouping) ───────────────────────────────────────────

def test_group_duplicates_reports_per_row_detail():
    from dashboard import practitioner_admin as pa
    rows = [
        {"id": "dc119bc6", "name": "Dr Glen", "email": "G@x.com", "portal_role": "coach",
         "modules_completed": 12, "wallet_balance_cents": 0,
         "wholesale_unlocked_at": "2026-06-01T00:00:00", "tier": "panel_certified",
         "city": None, "state": None, "created_at": None},
        {"id": "3c090c94", "name": "Remedy Match", "email": "g@x.com", "portal_role": None,
         "modules_completed": 0, "wallet_balance_cents": 0, "wholesale_unlocked_at": None,
         "tier": "org_member", "city": None, "state": None, "created_at": None},
        {"id": "c1", "name": "Aide One", "email": "front@clinic.com", "portal_role": None,
         "modules_completed": 0, "wallet_balance_cents": 0, "wholesale_unlocked_at": None,
         "tier": "org_member", "city": None, "state": None, "created_at": None},
        {"id": "c2", "name": "Aide Two", "email": "front@clinic.com", "portal_role": None,
         "modules_completed": 0, "wallet_balance_cents": 0, "wholesale_unlocked_at": None,
         "tier": "org_member", "city": None, "state": None, "created_at": None},
    ]
    out = pa.group_duplicates(rows, {"dc119bc6": {"orders": 2}})
    assert out["emails"] == 2
    assert out["rows"] == 4
    assert out["portal_conflicts"] == 0          # only one portal row per email here
    glen = [g for g in out["groups"] if g["email"] == "g@x.com"][0]
    assert glen["portal_count"] == 1
    assert {r["id"] for r in glen["rows"]} == {"dc119bc6", "3c090c94"}
    real = [r for r in glen["rows"] if r["id"] == "dc119bc6"][0]
    assert real["orders"] == 2 and real["level"] == 12 and real["wholesale_access"] is True


def test_group_duplicates_counts_a_two_portal_email_as_a_conflict():
    from dashboard import practitioner_admin as pa
    rows = [
        {"id": "a", "name": "A", "email": "x@y.com", "portal_role": "coach",
         "modules_completed": 0, "wallet_balance_cents": 0, "wholesale_unlocked_at": None},
        {"id": "b", "name": "B", "email": "x@y.com", "portal_role": "licensed",
         "modules_completed": 0, "wallet_balance_cents": 0, "wholesale_unlocked_at": None},
    ]
    out = pa.group_duplicates(rows, {})
    assert out["portal_conflicts"] == 1
    assert out["groups"][0]["portal_count"] == 2


def test_duplicate_query_covers_the_whole_table_not_just_portal_rows(monkeypatch):
    """The console roster only lists portal rows. The scraped directory lives in the
    same table and is exactly where the stub came from, so the audit must see it."""
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchall_result=[]))
    pa.duplicate_email_rows()
    # Picked by content, not by position: a catalogue probe runs first now.
    sql = [s for s in cur.sql() if "FROM practitioners WHERE lower(trim(email))" in s]
    assert len(sql) == 1, cur.sql()
    assert "portal_role IS NOT NULL" not in sql[0], sql[0]
    assert "HAVING COUNT(*) > 1" in sql[0], sql[0]


# ── the database backstop ─────────────────────────────────────────────────────

def test_index_creation_refuses_while_a_violation_exists(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[{"n": 3}]))
    out = pa.ensure_portal_email_unique_index()
    assert out["created"] is False
    assert out["blocked_by"] == 3
    assert not any("CREATE UNIQUE INDEX" in s for s in cur.sql())


def test_index_creation_failure_is_loud(monkeypatch, capsys):
    """A swallowed DDL failure would leave a green deploy enforcing nothing."""
    from dashboard import practitioner_admin as pa

    class _Boom(_FakeCur):
        def execute(self, sql, params=()):
            super().execute(sql, params)
            if "CREATE UNIQUE INDEX" in sql:
                raise RuntimeError("deadlock detected")

    _patch_cursor(monkeypatch, _Boom(fetchone_queue=[{"n": 0}]))
    with pytest.raises(RuntimeError):
        pa.ensure_portal_email_unique_index()
    assert "ux_practitioners_portal_email" in capsys.readouterr().out


def test_index_creation_runs_when_the_table_is_clean(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[{"n": 0}, {"present": 1}]))
    out = pa.ensure_portal_email_unique_index()
    ddl = [s for s in cur.sql() if "CREATE UNIQUE INDEX" in s]
    assert ddl, cur.sql()
    assert "lower(email)" in ddl[0] and "WHERE portal_role IS NOT NULL" in ddl[0]
    assert out["created"] is True and out["present"] is True


# ── duplicate_of column presence (fake cursor) ─────────────────────────────────

def test_duplicate_of_column_present_reads_the_catalogue_true(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[{"present": 1}]))
    assert pa.duplicate_of_column_present() is True
    sql = [s for s in cur.sql() if "information_schema.columns" in s]
    assert len(sql) == 1
    assert cur.executed[0][1] == ["practitioners", "duplicate_of"]


def test_duplicate_of_column_present_reads_the_catalogue_false(monkeypatch):
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    assert pa.duplicate_of_column_present() is False


# ── routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _key(appmod):
    return appmod.CONSOLE_SECRET or ""


def test_edit_retire_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "retire_practitioner",
                        lambda pid, db_path=None: calls.update({"pid": pid}) or
                        {"id": pid, "email": "g@x.com", "name": "Remedy Match",
                         "retired_role": "coach"})
    r = c.post("/api/console/practitioners/3c090c94/edit?key=" + _key(appmod),
               json={"action": "retire"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["retired_role"] == "coach"
    assert calls["pid"] == "3c090c94"


def test_edit_retire_blocked_is_readable(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _boom(pid, db_path=None):
        raise pa.RetireBlocked("Dr Glen", ["2 order(s) on record", "certification level 12"])

    monkeypatch.setattr(pa, "retire_practitioner", _boom)
    r = c.post("/api/console/practitioners/dc119bc6/edit?key=" + _key(appmod),
               json={"action": "retire"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["ok"] is False
    assert "order" in body["error"] and "level 12" in body["error"]
    assert body["attached"] == ["2 order(s) on record", "certification level 12"]


def test_edit_unretire_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "unretire_practitioner",
                        lambda pid, role: calls.update({"pid": pid, "role": role}) or
                        {"id": pid, "portal_role": role, "email": "g@x.com"})
    r = c.post("/api/console/practitioners/3c090c94/edit?key=" + _key(appmod),
               json={"action": "unretire", "role": "coach"})
    assert r.status_code == 200
    assert calls == {"pid": "3c090c94", "role": "coach"}


def test_edit_unretire_rejects_a_bad_role(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _bad(pid, role):
        raise ValueError("role must be one of licensed, coach, reseller")

    monkeypatch.setattr(pa, "unretire_practitioner", _bad)
    r = c.post("/api/console/practitioners/p1/edit?key=" + _key(appmod),
               json={"action": "unretire", "role": "wizard"})
    assert r.status_code == 400


def test_duplicates_endpoint_returns_groups(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "audit_duplicate_emails", lambda db_path=None: {
        "emails": 1, "rows": 2, "portal_conflicts": 0,
        "groups": [{"email": "g@x.com", "count": 2, "portal_count": 1, "rows": []}]})
    monkeypatch.setattr(pa, "portal_email_index_present", lambda: False)
    monkeypatch.setattr(pa, "duplicate_of_column_present", lambda: True)
    r = c.get("/api/console/practitioners/duplicates?key=" + _key(appmod))
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["emails"] == 1 and body["rows"] == 2
    assert body["index_present"] is False
    assert body["duplicate_of_present"] is True
    assert body["groups"][0]["email"] == "g@x.com"


def test_duplicates_endpoint_reports_the_column_missing(client, monkeypatch):
    """Before the migration lands, `duplicate_of_present` says so — it is not
    inferred from the audit succeeding, since the audit degrades gracefully in
    that window and would otherwise look identical either way."""
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "audit_duplicate_emails", lambda db_path=None: {
        "emails": 0, "rows": 0, "portal_conflicts": 0, "groups": []})
    monkeypatch.setattr(pa, "portal_email_index_present", lambda: True)
    monkeypatch.setattr(pa, "duplicate_of_column_present", lambda: False)
    r = c.get("/api/console/practitioners/duplicates?key=" + _key(appmod))
    assert r.status_code == 200
    assert r.get_json()["duplicate_of_present"] is False


def test_duplicates_endpoint_is_console_gated(client):
    c, appmod = client
    r = c.get("/api/console/practitioners/duplicates")
    if appmod.CONSOLE_SECRET:
        assert r.status_code == 401


def test_duplicates_endpoint_is_read_only(client, monkeypatch):
    c, appmod = client
    r = c.post("/api/console/practitioners/duplicates?key=" + _key(appmod), json={})
    assert r.status_code == 405


def test_email_index_endpoint_reports_a_block(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "ensure_portal_email_unique_index",
                        lambda: {"created": False, "present": False, "blocked_by": 3,
                                 "index": "ux_practitioners_portal_email"})
    r = c.post("/api/console/practitioners/email-index?key=" + _key(appmod), json={})
    assert r.status_code == 409
    body = r.get_json()
    assert body["ok"] is False and body["blocked_by"] == 3


def test_email_index_endpoint_reports_success(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "ensure_portal_email_unique_index",
                        lambda: {"created": True, "present": True, "blocked_by": 0,
                                 "index": "ux_practitioners_portal_email"})
    r = c.post("/api/console/practitioners/email-index?key=" + _key(appmod), json={})
    assert r.status_code == 200
    assert r.get_json()["present"] is True


# ── the duplicate-listing migration apply endpoint ─────────────────────────────

def test_duplicate_listing_migration_endpoint_is_console_gated(client):
    c, appmod = client
    r = c.post("/api/console/practitioners/duplicate-listing-migration", json={})
    if appmod.CONSOLE_SECRET:
        assert r.status_code == 401


def test_duplicate_listing_migration_endpoint_reports_success(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "apply_duplicate_listing_migration", lambda: {
        "migration": "practitioners-duplicate-listing", "applied": True,
        "duplicate_of_present": True, "view_columns": ["id", "tier"]})
    r = c.post("/api/console/practitioners/duplicate-listing-migration?key="
              + _key(appmod), json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["applied"] is True
    assert body["duplicate_of_present"] is True


def test_duplicate_listing_migration_endpoint_fails_loudly_on_a_partial_apply(
        client, monkeypatch):
    """A verification failure must not read as 200. The reason travels with it,
    not just a bare failure."""
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _boom():
        raise RuntimeError(
            "v_practitioners_public does not have its expected columns afterward")

    monkeypatch.setattr(pa, "apply_duplicate_listing_migration", _boom)
    r = c.post("/api/console/practitioners/duplicate-listing-migration?key="
              + _key(appmod), json={})
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert "expected columns" in body["error"]
