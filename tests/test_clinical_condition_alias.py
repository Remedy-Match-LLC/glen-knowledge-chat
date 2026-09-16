"""Glen, 2026-09-16: a button on a Clinical summary row to combine two conditions
that are really one, folding them into a single condition.

Stored as an ALIAS rather than by rewriting labels. The conditions come from
several stores — the portal intake, historical intakes, people.conditions, CRM tags
— and rewriting a label in one of them would leave the others disagreeing and could
not be undone. An alias folds them at read time, applies to every client, and is
removed by deleting one row.
"""
import sqlite3

import pytest

from dashboard.biofield_clinical_checklist import (
    alias_condition, aliases, build, ensure_alias_schema, profile_labels,
    remember_remedies, remedies_for, unalias_condition,
)


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    ensure_alias_schema(c)
    return c


def test_two_conditions_render_as_one_row(cx):
    alias_condition(cx, "Cataract", "Cataracts")
    labels = profile_labels({"conditions": ["Cataracts", "Cataract", "Dry eye"]}, cx=cx)
    assert labels == ["Cataracts", "Dry eye"]


def test_the_survivor_keeps_its_position_even_if_the_absorbed_came_first(cx):
    alias_condition(cx, "Cataract", "Cataracts")
    labels = profile_labels({"conditions": ["Cataract", "Dry eye", "Cataracts"]}, cx=cx)
    assert labels == ["Cataracts", "Dry eye"]


def test_matching_ignores_case_and_punctuation(cx):
    alias_condition(cx, "cataract", "Cataracts")
    assert profile_labels({"conditions": ["  CATARACT "]}, cx=cx) == ["Cataracts"]


def test_an_alias_is_reversible(cx):
    alias_condition(cx, "Cataract", "Cataracts")
    unalias_condition(cx, "Cataract")
    assert profile_labels({"conditions": ["Cataracts", "Cataract"]}, cx=cx) == \
        ["Cataracts", "Cataract"]


def test_a_condition_cannot_absorb_itself(cx):
    assert alias_condition(cx, "Cataracts", "Cataracts") is False
    assert aliases(cx) == {}


def test_a_chain_resolves_to_the_final_survivor(cx):
    """B folded into A, then A folded into C. B must land on C, not dangle at A."""
    alias_condition(cx, "Lens opacity", "Cataract")
    alias_condition(cx, "Cataract", "Cataracts")
    assert profile_labels({"conditions": ["Lens opacity"]}, cx=cx) == ["Cataracts"]


def test_a_cycle_does_not_hang(cx):
    alias_condition(cx, "A", "B")
    alias_condition(cx, "B", "A")
    assert profile_labels({"conditions": ["A"]}, cx=cx)


def test_the_absorbed_conditions_remedies_move_to_the_survivor(cx):
    remember_remedies(cx, "Cataract", ["Clear Lens Eyedrops"])
    remember_remedies(cx, "Cataracts", ["Macular Wellness Lutein"])
    alias_condition(cx, "Cataract", "Cataracts")
    got = [r["remedy"] if isinstance(r, dict) else r for r in remedies_for(cx, "Cataracts")]
    assert "Clear Lens Eyedrops" in got and "Macular Wellness Lutein" in got


def test_build_shows_one_row_for_an_aliased_pair(cx):
    rows = build({"conditions": ["Cataracts", "Cataract"]}, [], cx=cx)
    assert len(rows) == 2          # no alias yet
    alias_condition(cx, "Cataract", "Cataracts")
    rows = build({"conditions": ["Cataracts", "Cataract"]}, [], cx=cx)
    assert [r["label"] for r in rows] == ["Cataracts"]


def test_without_a_connection_nothing_changes(cx):
    """Every existing caller passes no cx and must behave exactly as before."""
    assert profile_labels({"conditions": ["Cataracts", "Cataract"]}) == \
        ["Cataracts", "Cataract"]
