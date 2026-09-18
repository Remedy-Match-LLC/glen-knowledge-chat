"""Erasing one client's health data, and provably not touching what must survive.

Glen, 2026-09-18: build the first slice, the health tables, with the opt-out and money
tables protected.
"""
import sqlite3

import pytest

from dashboard import client_erasure as CE


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    # a health table, a protected safety table, a protected money table
    c.execute("CREATE TABLE intake_responses (email TEXT, answers_json TEXT)")
    c.execute("CREATE TABLE scan_analyses (email TEXT, data TEXT)")
    c.execute("CREATE TABLE email_suppression (email TEXT, reason TEXT)")
    c.execute("CREATE TABLE purchase_history (email TEXT, slug TEXT)")
    for t, who in (("intake_responses", "victim@x.com"), ("intake_responses", "other@x.com"),
                   ("scan_analyses", "victim@x.com"),
                   ("email_suppression", "victim@x.com"),
                   ("purchase_history", "victim@x.com")):
        col = "answers_json" if t == "intake_responses" else ("reason" if t == "email_suppression"
              else ("slug" if t == "purchase_history" else "data"))
        c.execute(f"INSERT INTO {t} (email, {col}) VALUES (?, ?)", (who, "x"))
    c.commit()
    return c


def _count(cx, table, email="victim@x.com"):
    return cx.execute(f"SELECT COUNT(*) FROM {table} WHERE LOWER(email)=?", (email,)).fetchone()[0]


# --- the two invariants ------------------------------------------------------------

def test_health_and_protected_never_overlap():
    assert not (set(CE.HEALTH_TABLES) & CE.PROTECTED)


def test_the_engine_refuses_if_the_invariant_is_ever_broken(monkeypatch, cx):
    monkeypatch.setitem(CE.HEALTH_TABLES, "email_suppression", "email")
    with pytest.raises(AssertionError):
        CE.plan(cx, "victim@x.com")
    with pytest.raises(AssertionError):
        CE.erase(cx, "victim@x.com", confirm="victim@x.com")


# --- dry run -----------------------------------------------------------------------

def test_plan_counts_health_rows_and_deletes_nothing(cx):
    p = CE.plan(cx, "victim@x.com")
    assert p == {"intake_responses": 1, "scan_analyses": 1}
    assert _count(cx, "intake_responses") == 1, "plan must not delete"


def test_plan_skips_a_table_this_database_does_not_have(cx):
    # body_map_photos is in HEALTH_TABLES but not created here; must be skipped, not error.
    assert "body_map_photos" not in CE.plan(cx, "victim@x.com")


# --- erase -------------------------------------------------------------------------

def test_erase_removes_the_clients_health_rows(cx):
    removed = CE.erase(cx, "victim@x.com", confirm="victim@x.com")
    assert removed == {"intake_responses": 1, "scan_analyses": 1}
    assert _count(cx, "intake_responses") == 0
    assert _count(cx, "scan_analyses") == 0


def test_erase_leaves_another_clients_health_alone(cx):
    CE.erase(cx, "victim@x.com", confirm="victim@x.com")
    assert _count(cx, "intake_responses", "other@x.com") == 1


def test_erase_never_touches_a_protected_table(cx):
    CE.erase(cx, "victim@x.com", confirm="victim@x.com")
    assert _count(cx, "email_suppression") == 1, "an opt-out was deleted"
    assert _count(cx, "purchase_history") == 1, "a money record was deleted"


def test_erase_is_idempotent(cx):
    CE.erase(cx, "victim@x.com", confirm="victim@x.com")
    assert CE.erase(cx, "victim@x.com", confirm="victim@x.com") == {}


def test_erase_refuses_a_mismatched_confirm(cx):
    with pytest.raises(ValueError):
        CE.erase(cx, "victim@x.com", confirm="someone-else@x.com")
    assert _count(cx, "intake_responses") == 1, "it deleted despite refusing"


def test_erase_refuses_a_blank_email(cx):
    with pytest.raises(ValueError):
        CE.erase(cx, "", confirm="")


def test_the_match_is_case_insensitive(cx):
    cx.execute("INSERT INTO intake_responses (email, answers_json) VALUES ('VICTIM@X.com','y')")
    cx.commit()
    CE.erase(cx, "victim@x.com", confirm="victim@x.com")
    assert cx.execute("SELECT COUNT(*) FROM intake_responses "
                      "WHERE LOWER(email)='victim@x.com'").fetchone()[0] == 0


# --- the protected set is the real one ---------------------------------------------

def test_the_known_dangerous_tables_are_protected():
    for t in ("email_suppression", "purchase_history", "points_ledger", "subscriptions"):
        assert t in CE.PROTECTED, f"{t} must never be erasable"


def test_journal_entries_is_not_in_the_health_set():
    """It keys on a hardcoded user_id, not the client's email, and is erased per-session
    by the Delete-this-session box instead. Including it here would delete nothing (no
    email match) while implying it was covered."""
    assert "journal_entries" not in CE.HEALTH_TABLES
