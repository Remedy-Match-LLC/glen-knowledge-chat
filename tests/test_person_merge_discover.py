"""Which tables and columns a person merge moves.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import glob
import os
import pathlib
import re
import sqlite3

import pytest

from dashboard import db
from dashboard import person_merge_discover as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
DDL = [
    "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, tags TEXT)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, email TEXT, person_id INTEGER, total REAL, "
    "emailed_at TEXT)",
    "CREATE TABLE carts (email TEXT PRIMARY KEY, items TEXT)",
    "CREATE TABLE reveals (id INTEGER PRIMARY KEY, email TEXT, scan_date TEXT, "
    "UNIQUE(email, scan_date))",
    "CREATE TABLE coach_threads (id INTEGER PRIMARY KEY, coach_email TEXT, member_email TEXT)",
    "CREATE TABLE ghl_write_queue (id INTEGER PRIMARY KEY, email TEXT)",
    "CREATE TABLE households (id INTEGER PRIMARY KEY, head_person_id INTEGER)",
    "CREATE TABLE subs (id INTEGER PRIMARY KEY, email TEXT, status TEXT)",
    "CREATE UNIQUE INDEX subs_active ON subs(email) WHERE status='active'",
    "CREATE TABLE notes (body TEXT, email TEXT)",
]
TABLES = ("people", "orders", "carts", "reveals", "coach_threads", "ghl_write_queue",
          "households", "subs", "notes")


def _check(cx, schema):
    got = {(t.table, t.column, t.kind) for t in pd.targets(cx)
           if t.table in TABLES and t.schema == schema}
    assert ("orders", "email", "email") in got
    assert ("orders", "person_id", "person") in got
    assert ("orders", "emailed_at", "email") not in got          # a timestamp, not an address
    assert ("households", "head_person_id", "person") in got
    assert ("coach_threads", "coach_email", "email") in got
    assert ("coach_threads", "member_email", "email") in got
    assert ("notes", "body", "email") not in got
    assert ("email", "scan_date") in pd.unique_sets(cx, schema, "reveals")
    assert ("email",) in pd.unique_sets(cx, schema, "carts")
    assert pd.unique_sets(cx, schema, "subs") == [("id",)]      # the partial index is the database's
    assert pd.key_columns(cx, schema, "orders") == ["id"]
    assert pd.key_columns(cx, schema, "notes") is None
    assert "ghl_write_queue" in pd.HISTORY_TABLES
    assert "email_suppression" in pd.HISTORY_TABLES


def test_discovery_on_sqlite(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    for d in DDL:
        cx.execute(d)
    _check(cx, None)


def test_every_repo_table_is_named_move_or_history():
    """Spec test 6: a new table with an address or person column must be classified."""
    src = "".join(pathlib.Path(f).read_text() for f in
                  [str(ROOT / "app.py")] + glob.glob(str(ROOT / "dashboard" / "*.py")))
    found = pd.repo_tables_with_person_columns(src)
    named = pd.MOVE_TABLES | pd.HISTORY_TABLES | pd.MERGE_OWN_TABLES
    assert sorted(found - named) == []
    assert not (pd.MOVE_TABLES & pd.HISTORY_TABLES)


def _pg_ok():
    dsn = os.environ.get("PG_DSN", "")
    return bool(dsn) and "test" in dsn          # never a real database


@pytest.mark.skipif(not _pg_ok(), reason="PG_DSN for a test database not set")
def test_discovery_on_postgres_across_schemas(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    cx = db.connect("/data/pm_discover_test.db")
    other = db.connect("/data/pm_discover_other.db")
    try:
        schema = cx.execute("SELECT current_schema()").fetchone()[0]
        oschema = other.execute("SELECT current_schema()").fetchone()[0]
        assert schema != oschema
        for t in TABLES:
            cx.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        other.execute('DROP TABLE IF EXISTS "scans_elsewhere"')
        for d in DDL:
            cx.execute(d)
        other.execute("CREATE TABLE scans_elsewhere (id INTEGER PRIMARY KEY, email TEXT)")
        cx.commit()
        other.commit()
        _check(cx, schema)
        assert ("scans_elsewhere", "email") in {(t.table, t.column) for t in pd.targets(cx)
                                                if t.schema == oschema}
    finally:
        for t in TABLES:
            cx.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        other.execute('DROP TABLE IF EXISTS "scans_elsewhere"')
        cx.commit()
        other.commit()
        cx.close()
        other.close()


def test_sequence_sends_move_with_their_enrollments():
    """sequences._candidates joins sends to enrollments by address; they must move together."""
    assert "sequence_sends" in pd.MOVE_TABLES and "sequence_enrollments" in pd.MOVE_TABLES


def test_production_tables_found_on_2026_09_27_are_classified():
    """Listed from production by prod-queries/merge-unclassified-tables.py: a repo scan
    missed all 34 (lesson: feedback_a_repo_ddl_scan_is_not_the_table_list)."""
    move = {"biofield_month_grants", "biofield_reveal_spend_unlocks", "care_taster_grants",
            "coach_sub_charges", "coach_waitlist", "coaching_interest", "community_signals",
            "family_sub_charges", "family_sub_grants", "healing_oasis_requests", "journey_state",
            "member_interest", "membership_product_grants", "memberships",
            "order_membership_grants", "order_payments", "peer_interest", "peer_matches",
            "portal_library", "prepay_term_grants", "quiz_responses", "studio_credit_intents",
            "voice_signals"}
    history = {"fmp_clients", "escalation_queue", "identity_consolidation_audit",
               "email_identity_aliases", "inquiry_practitioners", "practitioner_inquiry_opt_outs",
               "practitioner_suggestions", "query_log", "remedy_match_email_sent",
               "review_link_tokens", "membership_reconcile_alerts"}
    assert move <= pd.MOVE_TABLES
    assert history <= pd.HISTORY_TABLES


def test_the_repo_scan_finds_tables_in_multi_statement_blocks():
    """memberships is created inside an executescript block; the first scan missed it."""
    src = (ROOT / "app.py").read_text()
    assert "memberships" in pd.repo_tables_with_person_columns(src)
