"""A client erasure after a merge reaches every address and the merge's own record.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md (review round 3)."""
import json
import sqlite3

import pytest

from dashboard import client_erasure as ce
from dashboard import person_aliases as pa
from dashboard import person_merge as pm
from dashboard import person_merge_discover as pd

AOL, GMAIL = "mel@aol.com", "mel@gmail.com"


def _fields(cx, s, m):
    cx.execute("DELETE FROM people WHERE id=?", (m,))


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT)")
    c.execute("CREATE TABLE client_facts (id INTEGER PRIMARY KEY, email TEXT, fact TEXT)")
    c.execute("CREATE TABLE intake_responses (email TEXT PRIMARY KEY, answers_json TEXT)")
    pa.init_tables(c)
    c.execute("INSERT INTO people VALUES (1, ?, 'Mel')", (AOL,))
    c.execute("INSERT INTO people VALUES (2, ?, 'Mel')", (GMAIL,))
    c.execute("INSERT INTO client_facts VALUES (1, ?, 'aol fact')", (AOL,))
    c.execute("INSERT INTO client_facts VALUES (2, ?, 'gmail fact')", (GMAIL,))
    c.execute("INSERT INTO intake_responses VALUES (?, 'aol answers')", (AOL,))
    c.execute("INSERT INTO intake_responses VALUES (?, 'gmail answers')", (GMAIL,))
    c.commit()
    return c


def _merge(cx, monkeypatch):
    monkeypatch.setitem(pm.CLASH_RULES, "intake_responses", "survivor")
    mid = pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                   applied_by="t", merge_people_fields=_fields)
    cx.commit()
    return mid


@pytest.mark.parametrize("named", [AOL, GMAIL])
def test_erasing_either_address_reaches_the_survivor(cx, monkeypatch, named):
    _merge(cx, monkeypatch)
    assert ce.plan(cx, named)["client_facts"] == 2
    ce.erase(cx, named, confirm=named)
    assert cx.execute("SELECT COUNT(*) FROM client_facts").fetchone()[0] == 0
    assert cx.execute("SELECT COUNT(*) FROM intake_responses").fetchone()[0] == 0


def test_erasure_purges_health_rows_from_the_merge_record(cx, monkeypatch):
    mid = _merge(cx, monkeypatch)
    assert cx.execute("SELECT COUNT(*) FROM person_merge_changes WHERE row_json LIKE '%aol answers%'"
                      ).fetchone()[0] == 1
    ce.erase(cx, GMAIL, confirm=GMAIL)
    assert cx.execute("SELECT COUNT(*) FROM person_merge_changes WHERE table_name IN "
                      "('client_facts','intake_responses')").fetchone()[0] == 0
    pm.undo(cx, mid, "t")
    cx.commit()
    assert cx.execute("SELECT COUNT(*) FROM intake_responses").fetchone()[0] == 0
    assert cx.execute("SELECT COUNT(*) FROM client_facts").fetchone()[0] == 0


def test_triage_invites_are_health_data_and_move():
    assert "triage_invites" in pd.MOVE_TABLES and "triage_invites" not in pd.HISTORY_TABLES
