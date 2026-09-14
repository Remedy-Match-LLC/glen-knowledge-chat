"""Console practitioner writers capitalise names. Glen, 2026-09-13."""
import pytest


class _Cur:
    def __init__(self):
        self.executed = []
        self._r = None

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.executed.append((s, list(params)))
        self._r = {"id": "P-NEW"} if ("INSERT INTO practitioners" in s
                                      and "RETURNING id" in s) else None

    def fetchone(self):
        return self._r


class _Ctx:
    def __init__(self, cur): self.cur = cur
    def __enter__(self): return self.cur
    def __exit__(self, *a): return False


@pytest.fixture
def cur(monkeypatch):
    c = _Cur()
    import db_supabase
    monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _Ctx(c))
    return c


def test_console_create_capitalises_the_name(cur):
    from dashboard import practitioner_admin as pa
    clean = {"email": "lm@x.com", "name": "lon mcrae", "portal_role": "licensed",
             "wholesale_access": False, "level": 0, "credentials": None,
             "list_in_finder": True, "city": None, "state": None}
    pa.create_or_update_practitioner(clean)
    inserts = [p for s, p in cur.executed if s.startswith("INSERT INTO practitioners")]
    assert inserts, "the insert path was not reached"
    assert inserts[0][1] == "Lon McRae"


def test_console_rename_writes_and_returns_the_capitalised_name(cur):
    from dashboard import practitioner_admin as pa
    assert pa.set_name("P1", "ANNA KREIMES") == "Anna Kreimes"
    assert cur.executed[0][1][0] == "Anna Kreimes"


def test_console_rename_keeps_a_mixed_case_name(cur):
    from dashboard import practitioner_admin as pa
    assert pa.set_name("P1", "Stacie Han") == "Stacie Han"
