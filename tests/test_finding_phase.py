"""Glen's Five Phases mapping, from his rulings on 2026-09-16."""
import sqlite3

import pytest

from dashboard.finding_phase import group_by_phase, phase_for


def test_most_et_terrains_are_viral_susceptibility_so_phase_one():
    for code in ("ET0", "ET1", "ET4", "ET7", "ET9", "ET10", "ET12", "ET15", "ET16"):
        assert phase_for(code) == 1, code


def test_the_two_named_exceptions():
    assert phase_for("ET14") == 2, "Bacterial Terrain"
    assert phase_for("ET13") == 3, "Fungal Terrain"


def test_the_liver_terrains_are_not_detox():
    """My first draft put Liver 1-3 in Phase 4 because detox reads like elimination.
    Glen's rule overrides that: they are viral susceptibility patterns."""
    for code in ("ET10", "ET11", "ET12"):
        assert phase_for(code) == 1


def test_the_autonomic_findings_are_phase_five():
    assert phase_for("ED6") == 5      # Heart Driver, sympathetic/parasympathetic
    assert phase_for("EI11") == 5     # Bone Marrow - Stomach Integrator


def test_an_unruled_finding_returns_none_rather_than_a_guess():
    """240 of the 262 findings have no phase yet. A wrong phase groups a layer around
    the wrong healing direction, which is worse than grouping on another axis."""
    for code in ("ER34", "ENV1", "NUT5", "ES4", "MB4", "MR3", "BFA-Grounding"):
        assert phase_for(code) is None, code


def test_matching_is_case_and_space_insensitive():
    assert phase_for("  et13 ") == 3


def test_blank_and_nonsense_are_none():
    for code in ("", None, "   ", "XYZ9"):
        assert phase_for(code) is None


def test_grouping_separates_the_unplaced():
    got = group_by_phase(["ET0", "ET13", "ET14", "ED6", "ER34"])
    assert got["by_phase"] == {1: ["ET0"], 3: ["ET13"], 2: ["ET14"], 5: ["ED6"]}
    assert got["unplaced"] == ["ER34"]


def test_phase_names_come_from_the_existing_module_not_a_second_set():
    """An earlier version of this file restated the names under different R-words.
    They belong to dashboard/terrain_phase and are reused."""
    from dashboard.finding_phase import clinical_name
    assert [clinical_name(n) for n in (1, 2, 3, 4, 5)] == [
        "Energize", "Rejuvenate", "Regenerate", "Cleanse", "Balance"]


def test_every_et_code_in_the_live_catalogue_gets_a_phase():
    """The rule is stated over ET as a family, so none may fall through.

    Reads Glen's e4l.db, which CI does not have. The connect itself raises on a
    missing file with mode=ro, so it has to be INSIDE the try: an earlier version
    guarded only the query and failed CI rather than skipping.
    """
    import os
    import sqlite3

    path = os.path.expanduser("~/AI-Training/e4l.db")
    try:
        cx = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    except Exception:
        pytest.skip("e4l.db not available (CI)")
    try:
        codes = [r[0] for r in cx.execute(
            "SELECT code FROM e4l_items WHERE code LIKE 'ET%'")]
    except Exception:
        pytest.skip("e4l_items not readable")
    finally:
        cx.close()
    if not codes:
        pytest.skip("no ET codes on file")
    assert all(phase_for(c) in (1, 2, 3) for c in codes)
