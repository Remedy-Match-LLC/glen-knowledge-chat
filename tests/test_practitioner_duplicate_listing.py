"""Hiding a duplicate listing from the public finder.

Repeated scrapes left 386 practitioners listed in the finder more than once.
`duplicate_of` names the row a listing was folded into, and
v_practitioners_public filters it out. It is a separate column from
removal_requested on purpose: that flag is the practitioner's own opt-out, and
answering "who asked to be removed from the directory?" must not return 131
people who never asked.

These are the pure and fake-cursor tests. The migration itself (the column, the
constraints, and the recreated view) runs against a real Postgres in
tests/test_practitioner_duplicate_listing_pg.py.
"""
import pytest


# ── fake Supabase cursor (same shape as tests/test_practitioner_email_unique.py) ──

class _FakeCur:
    def __init__(self, fetchone_queue=None):
        self.executed = []
        self._one = list(fetchone_queue or [])

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), list(params or ())))

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return []

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


def _row(pid, **kw):
    base = {"id": pid, "name": pid, "email": "front@clinic.com",
            "portal_role": None, "duplicate_of": None}
    base.update(kw)
    return base


# ── the refusals, pure ────────────────────────────────────────────────────────

def test_no_blockers_for_two_scraped_rows_on_one_email():
    """The control for every refusal below: the ordinary case must pass."""
    from dashboard.practitioner_admin import duplicate_mark_blockers
    assert duplicate_mark_blockers(_row("dup"), _row("keep")) == []


def test_an_unknown_target_is_refused():
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("dup"), None)
    assert reasons and "no practitioner with that id" in " ".join(reasons)


def test_two_different_emails_are_refused():
    """240 groups are genuinely different clinicians on a shared clinic inbox, so a
    shared email is the weakest evidence we accept. Rows that do not even share
    one are not known to be the same person at all."""
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("dup", email="a@x.com"),
                                      _row("keep", email="b@x.com"))
    assert reasons and "do not share an email" in " ".join(reasons)


def test_a_blank_email_on_either_side_is_refused():
    from dashboard.practitioner_admin import duplicate_mark_blockers
    assert duplicate_mark_blockers(_row("dup", email=None), _row("keep"))
    assert duplicate_mark_blockers(_row("dup"), _row("keep", email="  "))


def test_the_same_email_in_a_different_case_is_still_the_same_email():
    from dashboard.practitioner_admin import duplicate_mark_blockers
    assert duplicate_mark_blockers(_row("dup", email="Front@Clinic.com "),
                                   _row("keep", email="front@clinic.com")) == []


def test_a_portal_account_is_never_hidden_this_way():
    """A portal account is somebody's login. Hiding it silently would take their
    listing away without anything recording that a person did it."""
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("dup", portal_role="coach"), _row("keep"))
    assert reasons and "portal account" in " ".join(reasons)


def test_a_chain_is_refused_when_the_survivor_is_itself_a_duplicate():
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("dup"), _row("keep", duplicate_of="third"))
    assert reasons and "itself marked as a duplicate" in " ".join(reasons)


def test_a_chain_is_refused_from_the_other_end_too():
    """The same broken shape: hiding a row that is already somebody's survivor
    leaves them pointing at a listing nobody can see."""
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("dup"), _row("keep"), survivor_dependents=2)
    assert reasons and "hiding it would hide them too" in " ".join(reasons)


def test_a_row_cannot_be_a_duplicate_of_itself():
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(_row("same"), _row("same"))
    assert reasons and "duplicate of itself" in " ".join(reasons)


def test_every_refusal_reads_as_english_and_keeps_the_copy_rules():
    """The operator sees these strings. Two refusals at once must still read as
    one sentence, with no em dash and no bare double hyphen."""
    from dashboard.practitioner_admin import duplicate_mark_blockers
    reasons = duplicate_mark_blockers(
        _row("dup", portal_role="coach", email="a@x.com"), _row("keep", email="b@x.com"))
    assert len(reasons) == 2
    joined = " ".join(reasons)
    assert "--" not in joined and "—" not in joined
    assert joined.strip() and not joined.strip().endswith(".")


# ── mark / unmark against a cursor ────────────────────────────────────────────

def test_mark_writes_duplicate_of_and_touches_nothing_else(monkeypatch):
    """Only duplicate_of moves. The coordinates, the email and removal_requested
    all stay, which is what makes the undo exact and keeps the opt-out honest."""
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        _row("dup"), _row("keep"), {"n": 0}]))
    out = pa.mark_duplicate_of("dup", "keep")
    assert out["duplicate_of"] == "keep"
    updates = [(s, p) for s, p in cur.executed if s.startswith("UPDATE practitioners")]
    assert len(updates) == 1
    sql, params = updates[0]
    assert "duplicate_of=%s" in sql
    assert params == ["keep", "dup"]
    assert "removal_requested" not in sql
    assert "show_contact" not in sql
    assert "lat" not in sql
    assert not any("DELETE" in s.upper() for s in cur.sql())


def test_unmark_restores_the_listing_and_reports_what_it_undid(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        _row("dup", duplicate_of="keep")]))
    out = pa.unmark_duplicate("dup")
    assert out["was_duplicate_of"] == "keep"
    updates = [(s, p) for s, p in cur.executed if s.startswith("UPDATE practitioners")]
    assert len(updates) == 1
    assert "duplicate_of=NULL" in updates[0][0]
    assert updates[0][1] == ["dup"]


def test_mark_refuses_an_unknown_target_and_writes_nothing(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[_row("dup"), None]))
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of("dup", "ghost")
    assert "no practitioner with that id" in str(e.value)
    assert not [s for s in cur.sql() if s.startswith("UPDATE practitioners")]


def test_mark_refuses_a_portal_account_and_writes_nothing(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        _row("dup", portal_role="licensed"), _row("keep"), {"n": 0}]))
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of("dup", "keep")
    assert "portal account" in str(e.value)
    assert not [s for s in cur.sql() if s.startswith("UPDATE practitioners")]


def test_mark_refuses_different_emails_and_writes_nothing(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        _row("dup", email="a@x.com"), _row("keep", email="b@x.com"), {"n": 0}]))
    with pytest.raises(pa.DuplicateMarkBlocked):
        pa.mark_duplicate_of("dup", "keep")
    assert not [s for s in cur.sql() if s.startswith("UPDATE practitioners")]


def test_mark_refuses_a_chain_and_writes_nothing(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[
        _row("dup"), _row("keep", duplicate_of="third"), {"n": 0}]))
    with pytest.raises(pa.DuplicateMarkBlocked) as e:
        pa.mark_duplicate_of("dup", "keep")
    assert "itself marked as a duplicate" in str(e.value)
    assert not [s for s in cur.sql() if s.startswith("UPDATE practitioners")]


def test_mark_of_an_unknown_row_is_not_found(monkeypatch):
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    with pytest.raises(pa.PractitionerNotFound):
        pa.mark_duplicate_of("nope", "keep")


def test_unmark_of_an_unknown_row_is_not_found(monkeypatch):
    from dashboard import practitioner_admin as pa
    _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    with pytest.raises(pa.PractitionerNotFound):
        pa.unmark_duplicate("nope")


# ── the audit reads the same column the view filters on ───────────────────────

def test_the_audit_still_selects_duplicate_of():
    """group_duplicates cannot account for duplicate_of if the query never asks
    for it, and a fake cursor would not notice the column missing."""
    from dashboard.practitioner_admin import _DUP_COLS
    assert "duplicate_of" in [c.strip() for c in _DUP_COLS.split(",")]


def _dup_query(cur):
    return [s for s in cur.sql() if "FROM practitioners WHERE lower(trim(email))" in s][0]


def test_the_audit_asks_for_duplicate_of_once_the_column_is_there(monkeypatch):
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[{"present": 1}]))
    pa.duplicate_email_rows()
    assert "duplicate_of" in _dup_query(cur)


def test_the_audit_survives_the_window_before_the_migration_is_applied(monkeypatch, capsys):
    """The migration is applied to production BY HAND, after this code deploys.
    In that window duplicate_of does not exist and the audit, which worked before
    the column did, must not start 500ing. Nothing can be marked yet either, so
    the report is still correct; it just has to say so out loud."""
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    pa.duplicate_email_rows()
    sql = _dup_query(cur)
    assert "duplicate_of" not in sql
    assert "removal_requested" in sql and "lat" in sql     # the rest is intact
    assert "duplicate_of is missing" in capsys.readouterr().out


def test_the_probe_never_runs_the_query_that_would_abort_the_transaction(monkeypatch):
    """supabase_cursor runs with autocommit off, so probing by catching an error on
    the real SELECT would poison the transaction and make any retry inert."""
    from dashboard import practitioner_admin as pa
    cur = _patch_cursor(monkeypatch, _FakeCur(fetchone_queue=[None]))
    pa.duplicate_email_rows()
    assert cur.sql()[0].startswith("SELECT 1 AS present FROM information_schema.columns")
    assert len([s for s in cur.sql()
                if "FROM practitioners WHERE lower(trim(email))" in s]) == 1
