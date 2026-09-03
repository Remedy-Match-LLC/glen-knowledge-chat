"""Pure/writer tests for the fix_glued_name_suffixes re-runnable script.

This is a report-first, explicit-flag-to-write script (not a migration) that
finds and optionally corrects the practitioners rows whose name still carries
a source directory's glued-digit disambiguation artifact (AANP's
"Stacie Han2" for a second office). scrapers/practitioner_finder/db.py's
write boundary stops NEW scrapes from reintroducing it; this fixes rows
written before that fix landed.
"""
from scrapers.practitioner_finder.fix_glued_name_suffixes import (
    apply_renames,
    find_candidates,
)


def test_find_candidates_flags_glued_digit_names():
    rows = [
        {"id": "1", "name": "Stacie Han2", "source_url": "u/1"},
        {"id": "2", "name": "Dr. Paul Giordano2", "source_url": "u/2"},
        {"id": "3", "name": "Dr. Paul Giordano3", "source_url": "u/3"},
        {"id": "4", "name": "Nicole Egenberger2", "source_url": "u/4"},
        {"id": "5", "name": "Lisa Arnold2", "source_url": "u/5"},
    ]
    candidates = find_candidates(rows)
    assert [c[:3] for c in candidates] == [
        ("1", "u/1", "Stacie Han2"),
        ("2", "u/2", "Dr. Paul Giordano2"),
        ("3", "u/3", "Dr. Paul Giordano3"),
        ("4", "u/4", "Nicole Egenberger2"),
        ("5", "u/5", "Lisa Arnold2"),
    ]
    assert [c[3] for c in candidates] == [
        "Stacie Han", "Dr. Paul Giordano", "Dr. Paul Giordano",
        "Nicole Egenberger", "Lisa Arnold",
    ]


def test_find_candidates_skips_rows_that_do_not_match():
    rows = [
        {"id": "6", "name": "Farm 2", "source_url": "u/6"},
        {"id": "7", "name": "Zevan III", "source_url": "u/7"},
        {"id": "8", "name": "3M Dental", "source_url": "u/8"},
        {"id": "9", "name": None, "source_url": "u/9"},
    ]
    assert find_candidates(rows) == []


class _Cur:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class _Ctx:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self.cur

    def __exit__(self, *a):
        return False


def test_apply_renames_writes_only_the_new_name(monkeypatch):
    import scrapers.practitioner_finder.fix_glued_name_suffixes as mod
    cur = _Cur()
    monkeypatch.setattr(mod, "supabase_cursor", lambda: _Ctx(cur))
    candidates = [("1", "u/1", "Stacie Han2", "Stacie Han")]
    written = apply_renames(candidates)
    assert written == 1
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert "UPDATE practitioners" in sql
    assert params == ("Stacie Han", "1")


def test_apply_renames_empty_is_a_noop(monkeypatch):
    import scrapers.practitioner_finder.fix_glued_name_suffixes as mod
    cur = _Cur()
    monkeypatch.setattr(mod, "supabase_cursor", lambda: _Ctx(cur))
    assert apply_renames([]) == 0
    assert not cur.executed
