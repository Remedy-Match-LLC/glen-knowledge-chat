"""A Clinical layer is named by the pattern the "Stress pattern (head & tail)" field shows.

Glen, 2026-10-23, on Agnes Verches (test 56): "Instead of 'Macular Vascular Containment'
on layer 4, it put 'Wet AMD' which is the diagnosis." Her field showed the drafted
suggestion for Wet AMD. #2100 read only the stored table, so a condition whose pattern
was a suggestion, or was typed for this one test, kept the diagnosis as Head and Tail.

The page's order is: typed for this test, then stored, then the suggestion.
"""
import json
import sqlite3

import pytest

from biofield_local_app import create_app
from dashboard import biofield_clinical_head_term as ht
from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
from dashboard.biofield_clinical_checklist import remember_stress_pattern, suggested_pattern
from dashboard.biofield_clinical_proposals import save_pattern, save_selection
from dashboard.biofield_stress import init_stress_tables

WET = "Wet AMD"
WET_TERM = "Macular Vascular Containment"   # the seed's suggestion, stored nowhere


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


_NO_SCAN = {"status": "none", "found": False, "findings": [], "days_ago": None,
            "fresh": False}


def _setup(tmp_path, conditions, selections, stored=None, typed=None):
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        init_stress_tables(cx)
        tid = create_test(cx, "Agnes", "a@example.com", "2026-10-20")
        for label, pattern in (stored or {}).items():
            remember_stress_pattern(cx, label, pattern)
        for label, remedies in selections.items():
            save_selection(cx, tid, label, remedies)
        for label, pattern in (typed or {}).items():
            save_pattern(cx, tid, label, pattern)
        cx.commit()
    app = create_app(db, scan_lookup=lambda e: _NO_SCAN,
                     fetch_profile=lambda e: {"conditions": conditions},
                     fetch_recent_comms=lambda e: {},
                     interpret_complete=lambda s, u: json.dumps({"layers": []}))
    return db, tid, app.test_client()


def _rows(db, tid):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT id,head,most_affected FROM biofield_auth_chain "
                          "WHERE test_id=? ORDER BY layer,id", (int(str(tid).lstrip("a")),)
                          ).fetchall()


def _balances(db, tid, rid):
    with sqlite3.connect(db) as cx:
        return [r[0] for r in cx.execute(
            "SELECT s.label FROM biofield_auth_layer_stress ls JOIN biofield_auth_stress s "
            "ON s.id=ls.stress_id WHERE ls.test_id=? AND ls.chain_rid=? ORDER BY s.id",
            (int(str(tid).lstrip("a")), rid))]


def _program(client, tid):
    j = client.post(f"/author/{tid}/program", json={"mode": "minimum", "apply": True}).get_json()
    assert j["ok"] and j["applied"], j
    return j


def test_the_seed_really_suggests_the_term():
    # The case rests on this: if the seed changes, the test below proves nothing.
    assert suggested_pattern(WET) == WET_TERM


def test_a_suggested_pattern_names_the_layer_never_the_diagnosis(tmp_path):
    db, tid, client = _setup(tmp_path, [WET], {WET: ["AngiogenX"]})
    _program(client, tid)
    (rid, head, tail), = _rows(db, tid)
    assert (head, tail) == (WET_TERM, WET_TERM)
    assert _balances(db, tid, rid) == [WET]
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG not in page


def test_a_pattern_typed_for_this_test_wins_over_stored_and_suggested(tmp_path):
    db, tid, client = _setup(tmp_path, [WET], {WET: ["AngiogenX"]},
                             stored={WET: "Macular Stored"}, typed={WET: "Macular Typed"})
    _program(client, tid)
    (rid, head, tail), = _rows(db, tid)
    assert (head, tail) == ("Macular Typed", "Macular Typed")
    assert _balances(db, tid, rid) == [WET]


def test_an_existing_diagnosis_layer_is_corrected_to_the_shown_pattern(tmp_path):
    """Agnes's layer 4 as it stands: four rows headed "Wet AMD", written before the fix."""
    db, tid, _client = _setup(tmp_path, [WET], {WET: ["AngiogenX", "WholOmega"]})
    with sqlite3.connect(db) as cx:
        for n, remedy in enumerate(["AngiogenX", "WholOmega"], start=8):
            add_chain_row(cx, tid, n, WET, WET, remedy, origin="program")
        cx.commit()
        before = cx.total_changes
        plan = ht.correct_chain(cx, tid)              # dry run writes nothing
        assert [(r["new_head"], r["new_tail"]) for r in plan["rows"]] == [(WET_TERM, WET_TERM)] * 2
        # Checked on THIS connection, so an uncommitted write by the dry run shows too
        # (review round 2: a second connection cannot see one).
        assert cx.total_changes == before and not cx.in_transaction
        assert {(h, t) for h, t in cx.execute(
            "SELECT head, most_affected FROM biofield_auth_chain WHERE test_id=?",
            (int(tid.lstrip("a")),))} == {(WET, WET)}
        assert cx.execute("SELECT COUNT(*) FROM biofield_auth_layer_stress").fetchone()[0] == 0
        ht.correct_chain(cx, tid, dry_run=False)
    rows = _rows(db, tid)
    assert {(h, t) for _, h, t in rows} == {(WET_TERM, WET_TERM)}
    assert all(_balances(db, tid, rid) == [WET] for rid, _, _ in rows)


def test_no_pattern_anywhere_is_left_out_never_named_by_its_wording(tmp_path):
    label = "Some free-text worry nobody catalogued"
    db, tid, _client = _setup(tmp_path, [label], {label: ["Remedy A"]})
    with sqlite3.connect(db) as cx:
        assert ht._key(label) not in ht.conditions(cx, tid)
        assert ht.shown_pattern(cx, label, {}, {}) == ""


def test_a_card_is_not_flagged_as_patternless_when_the_field_shows_a_pattern(tmp_path):
    """An uncorrected "Wet AMD" card: its field shows a suggestion, so "No stress pattern
    yet" would be untrue. A free-text condition with none is still flagged."""
    free = "Some free-text worry nobody catalogued"
    db, tid, client = _setup(tmp_path, [WET, free], {WET: ["AngiogenX"], free: ["Remedy A"]})
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, WET, WET, "AngiogenX", origin="program")
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert ht.NO_PATTERN_FLAG not in page
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 2, free, free, "Remedy A", origin="program")
    page = client.get(f"/author/{tid}").get_data(as_text=True)
    assert page.count(ht.NO_PATTERN_FLAG) == 1


def test_a_pattern_typed_under_the_numeric_id_never_names_the_intakes_layer(tmp_path):
    """Intake "a1" and numeric test "1" are different reports (review round 1). The page
    reads typed patterns by the exact id, so the layer must too."""
    db, tid, client = _setup(tmp_path, [WET], {WET: ["AngiogenX"]})
    assert tid == "a1"
    with sqlite3.connect(db) as cx:
        save_pattern(cx, "1", WET, "Macular Numeric")
        cx.commit()
        assert ht.conditions(cx, "1")[ht._key(WET)][1] == "Macular Numeric"
        assert ht.conditions(cx, tid)[ht._key(WET)][1] == WET_TERM
    _program(client, tid)
    (_rid, head, tail), = _rows(db, tid)
    assert (head, tail) == (WET_TERM, WET_TERM)


def test_an_absorbed_condition_takes_its_survivors_combined_term(tmp_path):
    """The page folds "Difficulty seeing in low light" into "Dry AMD" and shows one row
    with both functions. Its wording on a layer must take that term, not its own."""
    from dashboard.biofield_clinical_checklist import alias_condition
    low = "Difficulty seeing in low light"
    db, tid, _client = _setup(tmp_path, [low], {low: ["Remedy A"]},
                              stored={"Dry AMD": "Macular Resilience", low: "Dark Adaptation"})
    with sqlite3.connect(db) as cx:
        assert alias_condition(cx, low, "Dry AMD")
        assert ht.conditions(cx, tid)[ht._key(low)] == (
            low, "Macular Resilience & Dark Adaptation")


def test_reading_conditions_never_creates_the_stress_table():
    """conditions() is a read, used by dry runs on read-only copies (review round 3). An
    absorbed condition with no stored pattern must not reach stress_pattern(), which
    creates biofield_clinical_stress."""
    from dashboard.biofield_clinical_checklist import alias_condition
    from dashboard.biofield_stress import _table_exists
    cx = sqlite3.connect(":memory:")
    init_auth_tables(cx)
    init_stress_tables(cx)
    save_selection(cx, "a1", WET, ["AngiogenX"])
    assert alias_condition(cx, "Some unseeded worry", WET)
    cx.commit()
    assert not _table_exists(cx, "biofield_clinical_stress")
    assert ht.conditions(cx, "a1")[ht._key(WET)][1] == WET_TERM
    assert not _table_exists(cx, "biofield_clinical_stress")


def test_correct_chain_uses_the_pattern_typed_for_the_id_given(tmp_path):
    """plan_correction must look typed patterns up by the id it was given, "a1", never
    by its number (review round 3)."""
    db, tid, _client = _setup(tmp_path, [WET], {WET: ["AngiogenX"]},
                              typed={WET: "Macular Typed"})
    assert tid == "a1"
    with sqlite3.connect(db) as cx:
        add_chain_row(cx, tid, 1, WET, WET, "AngiogenX", origin="program")
        plan = ht.correct_chain(cx, tid)
        assert [(r["new_head"], r["new_tail"]) for r in plan["rows"]] == [
            ("Macular Typed", "Macular Typed")]
        ht.correct_chain(cx, tid, dry_run=False)
    assert [(h, t) for _, h, t in _rows(db, tid)] == [("Macular Typed", "Macular Typed")]
