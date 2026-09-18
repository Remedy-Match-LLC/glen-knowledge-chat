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


# ── Combining carries BOTH cards' remedies ───────────────────────────────────────
# Glen, 2026-09-18: "When combining cards in Clinical summary, combine/add both sets
# of remedies." Hand-added remedies already travelled. The two INHERITED sets did
# not: the absorbed condition's own program list, and its FileMaker history. Both
# stayed keyed to the name he had just folded away, so they left the row.

def test_the_absorbed_conditions_program_list_reaches_the_survivor(cx):
    """'Dry eye' carries its own two remedies. Folded into a condition with none of
    them, both must still be offered."""
    alias_condition(cx, "Dry eye", "Cataracts")
    got = {r["remedy"] for r in remedies_for(cx, "Cataracts")}
    assert {"ACES Eye Drops", "WholOmega"} <= got


def test_the_absorbed_conditions_history_reaches_the_survivor(cx):
    """FileMaker history is fetched per label by the caller, so a fold lost it."""
    history = {"Cataract": [{"remedy": "Clear Lens Eyedrops", "count": 12}],
               "Cataracts": [{"remedy": "Macular Wellness Lutein", "count": 3}]}
    alias_condition(cx, "Cataract", "Cataracts")
    got = {r["remedy"] for r in remedies_for(
        cx, "Cataracts", historical=history["Cataracts"],
        history_lookup=lambda label: history.get(label, []))}
    assert {"Clear Lens Eyedrops", "Macular Wellness Lutein"} <= got


def test_the_survivors_own_history_still_leads(cx):
    """Order is unchanged: the surviving card's own remedies come first."""
    history = {"Cataract": [{"remedy": "Folded One", "count": 99}],
               "Cataracts": [{"remedy": "Survivor One", "count": 1}]}
    alias_condition(cx, "Cataract", "Cataracts")
    got = [r["remedy"] for r in remedies_for(
        cx, "Cataracts", historical=history["Cataracts"],
        history_lookup=lambda label: history.get(label, []))]
    assert got.index("Survivor One") < got.index("Folded One")


def test_a_remedy_on_both_cards_is_offered_once(cx):
    history = {"Cataract": [{"remedy": "Shared One", "count": 5}],
               "Cataracts": [{"remedy": "Shared One", "count": 2}]}
    alias_condition(cx, "Cataract", "Cataracts")
    got = [r["remedy"] for r in remedies_for(
        cx, "Cataracts", historical=history["Cataracts"],
        history_lookup=lambda label: history.get(label, []))]
    assert got.count("Shared One") == 1


def test_a_forgotten_remedy_stays_hidden_when_it_arrives_by_a_fold(cx):
    """forget_remedy must win whichever card supplies it, the rule remedies_for
    already applies to its other three sources."""
    from dashboard.biofield_clinical_checklist import forget_remedy
    alias_condition(cx, "Dry eye", "Cataracts")
    forget_remedy(cx, "Cataracts", "ACES Eye Drops")
    got = {r["remedy"] for r in remedies_for(cx, "Cataracts")}
    assert "ACES Eye Drops" not in got and "WholOmega" in got


def test_without_a_history_lookup_nothing_changes(cx):
    """Every existing caller passes no lookup, so the fold must not require one."""
    alias_condition(cx, "Cataract", "Cataracts")
    assert remedies_for(cx, "Cataracts", historical=[]) is not None


# ── A chain of folds carries all the way ─────────────────────────────────────────
# Glen, 2026-09-18, asked whether a chained fold carries too: "yes". His own data has
# one: low estrogen -> low progesterone -> Adrenal Fatigue. absorbed_by returned only
# the direct fold, so the first card's remedies stopped one step short of the last
# survivor. Labels already resolved through a chain; remedies did not follow them.

def test_a_chained_folds_remedies_reach_the_final_survivor(cx):
    remember_remedies(cx, "Lens opacity", ["From the first card"])
    remember_remedies(cx, "Cataract", ["From the middle card"])
    alias_condition(cx, "Lens opacity", "Cataract")
    alias_condition(cx, "Cataract", "Cataracts")
    got = {r["remedy"] for r in remedies_for(cx, "Cataracts")}
    assert {"From the first card", "From the middle card"} <= got


def test_a_chain_does_not_reach_sideways(cx):
    """Two conditions folded into the same survivor are siblings, not a chain. The
    survivor gets both; neither gets the other's."""
    remember_remedies(cx, "Left one", ["Only on the left"])
    remember_remedies(cx, "Right one", ["Only on the right"])
    alias_condition(cx, "Left one", "Cataracts")
    alias_condition(cx, "Right one", "Cataracts")
    got = {r["remedy"] for r in remedies_for(cx, "Cataracts")}
    assert {"Only on the left", "Only on the right"} <= got
    assert "Only on the right" not in {r["remedy"] for r in remedies_for(cx, "Left one")}


def test_a_cycle_of_folds_does_not_hang_the_remedy_walk(cx):
    """A folded into B and B into A. The walk must terminate, as the label walk does."""
    remember_remedies(cx, "A", ["From A"])
    remember_remedies(cx, "B", ["From B"])
    alias_condition(cx, "A", "B")
    alias_condition(cx, "B", "A")
    got = {r["remedy"] for r in remedies_for(cx, "B")}
    assert "From A" in got
