"""A folded condition brings its stress pattern with it.

Glen, 2026-09-16, on combining two Clinical Summary rows: "allow items to be combined into
one (list the two names, add together any remedies listed and their states)". Then, after
the combine button shipped: "Still no function to combine two stress patterns in Clinical
Summary."

He was right. #1704 moved the absorbed condition's remembered remedies to the survivor and
left its pattern behind. A pattern is the function to restore rather than the pathology,
so folding "Difficulty seeing in low light" into "Dry AMD" kept Macular Resilience and
silently dropped Dark Adaptation. A combined condition needs both restored.

The merge is ADDITIVE. The survivor's own wording always leads and is never rewritten,
because it may be a term the practitioner typed for this client.
"""
import sqlite3

import pytest

from dashboard import biofield_clinical_checklist as cc


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    cc.ensure_alias_schema(c)
    cc.ensure_stress_schema(c)
    yield c
    c.close()


def test_both_functions_survive_the_fold():
    assert cc.combine_patterns("Macular Resilience", ["Dark Adaptation"]) == \
        "Macular Resilience & Dark Adaptation"


def test_the_survivors_wording_leads():
    """It may be what the practitioner typed for this client. It is never reordered."""
    out = cc.combine_patterns("Central Acuity", ["Vitreous Clarity", "Dark Adaptation"])
    assert out.startswith("Central Acuity")
    assert out == "Central Acuity & Vitreous Clarity & Dark Adaptation"


def test_a_function_already_present_is_not_repeated():
    assert cc.combine_patterns("Central Acuity", ["Central Acuity"]) == "Central Acuity"
    assert cc.combine_patterns("Central Acuity & Dark Adaptation", ["Dark Adaptation"]) == \
        "Central Acuity & Dark Adaptation"


def test_containment_ignores_case_and_spacing():
    assert cc.combine_patterns("Central Acuity", ["  central   acuity "]) == "Central Acuity"


def test_an_empty_survivor_takes_the_absorbed_function():
    assert cc.combine_patterns("", ["Dark Adaptation"]) == "Dark Adaptation"


def test_nothing_absorbed_changes_nothing():
    assert cc.combine_patterns("Macular Resilience", []) == "Macular Resilience"
    assert cc.combine_patterns("Macular Resilience", None) == "Macular Resilience"


def test_the_absorbed_conditions_recorded_pattern_is_used(cx):
    cc.remember_stress_pattern(cx, "Night Blindness", "Dark Adaptation")
    cc.alias_condition(cx, "Night Blindness", "Dry AMD")
    assert cc.absorbed_patterns(cx, "Dry AMD") == ["Dark Adaptation"]


def test_a_seeded_suggestion_is_not_lost_just_because_nobody_typed_over_it(cx):
    """"Difficulty seeing in low light" has a drafted term and no recorded one."""
    assert cc.suggested_pattern("Difficulty seeing in low light") == "Dark Adaptation"
    cc.alias_condition(cx, "Difficulty seeing in low light", "Dry AMD")
    assert cc.absorbed_patterns(cx, "Dry AMD") == ["Dark Adaptation"]


def test_what_the_practitioner_recorded_beats_the_suggestion(cx):
    cc.remember_stress_pattern(cx, "Difficulty seeing in low light", "Scotopic Function")
    cc.alias_condition(cx, "Difficulty seeing in low light", "Dry AMD")
    assert cc.absorbed_patterns(cx, "Dry AMD") == ["Scotopic Function"]


def test_no_connection_is_safe():
    """Every existing caller passes no connection and must behave as before."""
    assert cc.absorbed_patterns(None, "Dry AMD") == []


def test_the_built_row_shows_the_combined_pattern(cx):
    """End to end through build(), which is what the checklist renders."""
    cc.alias_condition(cx, "Difficulty seeing in low light", "Dry AMD")
    rows = cc.build({"conditions": ["Dry AMD"]}, [], cx=cx)
    row = next((r for r in rows if r["label"] == "Dry AMD"), None)
    assert row is not None, "the survivor must still be on the checklist"
    assert "Macular Resilience" in row["stress_pattern"]
    assert "Dark Adaptation" in row["stress_pattern"], (
        "the folded condition's function was dropped, which is the bug Glen reported"
    )


def test_the_absorbed_condition_no_longer_appears_on_its_own(cx):
    """#1704's behaviour, unchanged by this. Combining means one row, not two."""
    cc.alias_condition(cx, "Difficulty seeing in low light", "Dry AMD")
    rows = cc.build({"conditions": ["Dry AMD", "Difficulty seeing in low light"]}, [], cx=cx)
    labels = [r["label"] for r in rows]
    assert "Difficulty seeing in low light" not in labels
