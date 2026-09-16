"""Glen, 2026-09-16: "Add the checked 'Stress pattern's from Clinical summary to the
list of Stresses so they can be better coordinated together in creating layers."

The Clinical summary and the Stresses panel were two separate lists. The set-cover
behind minimal remedies already spans scan and non-scan stresses, so once a clinical
pattern IS a stress it is coordinated with the energetic ones for free — no new
solver, just the bridge.

It is the STRESS PATTERN that crosses, never the condition label. The chain speaks in
stress patterns rather than the client's own words for a condition, which is why
`balance_item` takes a `pattern` argument and prefers it over the label.
"""
import sqlite3

import pytest

from dashboard.biofield_clinical_checklist import clinical_patterns_as_stresses
from dashboard.biofield_stress import init_stress_tables, list_stresses


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    init_stress_tables(c)
    return c


def _item(label, pattern="", checked=False):
    return {"label": label, "stress_pattern": pattern, "checked": checked}


def test_a_checked_item_crosses_as_its_pattern_not_its_label(cx):
    added = clinical_patterns_as_stresses(cx, "a5", [
        _item("Cataracts", "Lens clarity", checked=True)])
    assert added == ["Lens clarity"]
    labels = [r[0] for r in cx.execute("SELECT label FROM biofield_auth_stress")]
    assert labels == ["Lens clarity"]
    assert "Cataracts" not in labels


def test_an_unchecked_item_does_not_cross(cx):
    assert clinical_patterns_as_stresses(cx, "a5", [
        _item("Cataracts", "Lens clarity", checked=False)]) == []


def test_an_item_with_no_pattern_is_skipped_not_guessed(cx):
    """Falling back to the condition label would put the client's own words into the
    causal chain, which is the thing stress patterns exist to avoid."""
    added = clinical_patterns_as_stresses(cx, "a5", [_item("Cataracts", "", checked=True)])
    assert added == []
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0] == 0


def test_clicking_twice_adds_nothing_the_second_time(cx):
    items = [_item("Cataracts", "Lens clarity", checked=True)]
    first = clinical_patterns_as_stresses(cx, "a5", items)
    second = clinical_patterns_as_stresses(cx, "a5", items)
    assert first == ["Lens clarity"] and second == []
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0] == 1


def test_two_conditions_sharing_a_pattern_make_one_stress(cx):
    """Which is the point of coordinating them: one pattern is one thing to balance."""
    added = clinical_patterns_as_stresses(cx, "a5", [
        _item("Cataracts", "Lens clarity", checked=True),
        _item("Lens opacity", "Lens clarity", checked=True)])
    assert added == ["Lens clarity"]
    assert cx.execute("SELECT COUNT(*) FROM biofield_auth_stress").fetchone()[0] == 1


def test_it_lands_as_required_so_the_minimal_set_must_cover_it(cx):
    clinical_patterns_as_stresses(cx, "a5", [_item("Cataracts", "Lens clarity", True)])
    row = cx.execute("SELECT source, balance FROM biofield_auth_stress").fetchone()
    assert row == ("clinical", "required")


def test_a_clinical_stress_is_editable_because_it_is_not_a_scan(cx):
    """source='clinical' gets Edit and Delete, the same as transcript and comms."""
    from dashboard.biofield_report_html import render_stress_panel
    clinical_patterns_as_stresses(cx, "a5", [_item("Cataracts", "Lens clarity", True)])
    sid = cx.execute("SELECT id FROM biofield_auth_stress").fetchone()[0]
    html = render_stress_panel({"by_layer": [], "unassigned": [
        {"id": sid, "code": "lens clarity", "label": "Lens clarity",
         "source": "clinical", "balance": "required", "balanced_by": ""}]})
    assert f"renameStress({sid}" in html and f"deleteStress({sid}" in html


def test_the_button_and_handler_are_on_the_page():
    from dashboard.biofield_fee import build_fee_state
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "", clinical_checklist=[
        {"label": "Cataracts", "checked": True, "covered_by": "", "layer": None,
         "common_remedies": [], "stress_pattern": "Lens clarity",
         "remembered_pattern": "", "pattern_is_suggested": False}],
        fee_state=build_fee_state("s@f.co", lambda _e: {"available": True}))
    assert "Add checked patterns" in h
    assert "function clinicalToStresses" in h
    assert "/author/a7/clinical-items/to-stresses" in h
