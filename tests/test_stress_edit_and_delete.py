"""Glen, 2026-09-16: "I need to be able to edit and/or delete stresses added from
transcript in Biofield Intake."

Transcript-interpreted stresses are stored with source='voice'
(`interpret_stresses` -> `add_voice_stress`). The Delete control only rendered for
source='tag', so the ones he actually wanted to remove had no control at all, even
though the route and the store function already worked for any source.

Rename did not exist. It has to move the coverage rows too: coverage is keyed on
`code`, which is derived from the label, so renaming without it silently orphans
every remedy that was covering that stress.
"""
import sqlite3

import pytest

from dashboard.biofield_stress import (
    add_stress, init_stress_tables, rename_stress, stress_id_for,
)


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    init_stress_tables(c)
    return c


def test_rename_changes_the_label(cx):
    add_stress(cx, "a5", "Adrenal Fatig", source="voice")
    sid = stress_id_for(cx, "a5", "Adrenal Fatig")
    assert rename_stress(cx, "a5", sid, "Adrenal Fatigue") is True
    rows = cx.execute("SELECT label FROM biofield_auth_stress WHERE id=?", (sid,)).fetchall()
    assert rows[0][0] == "Adrenal Fatigue"


def test_rename_moves_the_code_so_coverage_follows(cx):
    """Coverage is keyed on the normalised label. A rename that leaves the old code
    behind orphans every remedy covering it, and the stress silently reads unbalanced."""
    add_stress(cx, "a5", "Adrenal Fatig", source="voice")
    sid = stress_id_for(cx, "a5", "Adrenal Fatig")
    cx.execute("INSERT INTO biofield_auth_remedy_coverage(test_id,remedy,code) "
               "VALUES(5,'Adrenal Syntropy Sublingual Powder','adrenal fatig')")
    cx.commit()
    before = cx.execute("SELECT COUNT(*) FROM biofield_auth_remedy_coverage "
                        "WHERE test_id=5").fetchone()[0]
    rename_stress(cx, "a5", sid, "Adrenal Fatigue")
    after = cx.execute("SELECT COUNT(*) FROM biofield_auth_remedy_coverage "
                       "WHERE test_id=5").fetchone()[0]
    assert before == after, "coverage rows were orphaned by the rename"
    old = cx.execute("SELECT COUNT(*) FROM biofield_auth_remedy_coverage "
                     "WHERE test_id=5 AND code='adrenal fatig'").fetchone()[0]
    assert old == 0, "a coverage row still points at the old code"


def test_rename_to_a_name_already_on_this_test_is_refused(cx):
    add_stress(cx, "a5", "Adrenal Fatigue", source="voice")
    add_stress(cx, "a5", "Thyroid", source="voice")
    sid = stress_id_for(cx, "a5", "Thyroid")
    assert rename_stress(cx, "a5", sid, "Adrenal Fatigue") is False
    assert stress_id_for(cx, "a5", "Thyroid") == sid


def test_rename_rejects_an_empty_label(cx):
    add_stress(cx, "a5", "Thyroid", source="voice")
    sid = stress_id_for(cx, "a5", "Thyroid")
    assert rename_stress(cx, "a5", sid, "   ") is False


def test_rename_will_not_touch_another_test(cx):
    add_stress(cx, "a5", "Thyroid", source="voice")
    sid = stress_id_for(cx, "a5", "Thyroid")
    assert rename_stress(cx, "a9", sid, "Renamed") is False


def test_a_transcript_stress_offers_delete_and_edit():
    """The control is the whole point: source='voice' had neither."""
    from dashboard.biofield_report_html import render_stress_panel
    # "by_layer" selects the grouped branch, the one that renders "unassigned".
    data = {"by_layer": [], "unassigned": [{"id": 3, "code": "adrenal fatigue", "label": "Adrenal Fatigue",
                            "source": "voice", "balance": "required", "balanced_by": ""}]}
    html = render_stress_panel(data)
    assert "deleteStress(3" in html
    assert "renameStress(3" in html


def test_a_scan_stress_offers_neither():
    """Scan-derived stresses come from the E4L scan itself; editing one would put the
    intake at odds with the scan it was built from."""
    from dashboard.biofield_report_html import render_stress_panel
    data = {"by_layer": [], "unassigned": [{"id": 4, "code": "er34", "label": "Adrenals",
                            "source": "scan", "balance": "required", "balanced_by": ""}]}
    html = render_stress_panel(data)
    assert "deleteStress(4" not in html
    assert "renameStress(4" not in html
