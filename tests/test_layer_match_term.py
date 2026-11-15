"""Live session part 2a: one spoken phrase matched to one layer field.

Glen's rulings of 14 November 2026 (platform/plans/2026-11-07-live-session-segments.md):
4 an unmatched phrase is kept and says so, 5 "head and tail" before or after fills both,
9 a spoken dose fills dose and frequency, else the catalogue default fills in.
"""
import sqlite3

import pytest

from dashboard.biofield_authoring import (init_auth_tables, resolve_remedy_match,
                                          resolve_remedy_name, resolve_stress_match,
                                          resolve_stress_name)
from dashboard.biofield_layer_match import is_balanced_cue, match_term, parse_dose


@pytest.fixture
def cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    cx.execute("CREATE TABLE fmp_snap_products(id_pk TEXT,product_name TEXT,dosage TEXT,"
               "dosage_freq TEXT,dosage_timing TEXT)")
    cx.executemany("INSERT INTO fmp_snap_products VALUES(?,?,?,?,?)", [
        ("1", "Sulfur Syntropy", "1 capsule", "twice a day", "with food, or as guided."),
        ("2", "Perelandra Rose Essence", "3 drops", "3 times a day", "under the tongue"),
    ])
    cx.execute("CREATE TABLE fmp_snap_client_active_main_stress(id_pk TEXT, main_stress TEXT)")
    cx.executemany("INSERT INTO fmp_snap_client_active_main_stress VALUES(?,?)",
                   [("1", "Liver Congestion"), ("2", "Heavy Metal Toxicity")])
    cx.commit()
    return cx


# ---- remedies -----------------------------------------------------------------

def test_misheard_remedy_matches_the_catalogue_name(cx):
    m = match_term(cx, "remedy", "sulfur centropy")
    assert m["value"] == "Sulfur Syntropy" and m["matched"] is True


def test_unknown_remedy_is_kept_and_not_matched(cx):
    m = match_term(cx, "remedy", "zzqx florpwhistle")
    assert m["matched"] is False
    assert m["value"] == "Zzqx Florpwhistle"          # the words stay, Title Cased


def test_spoken_dose_fills_dose_and_frequency(cx):
    m = match_term(cx, "remedy", "Sulfur Syntropy 10 drops twice a day")
    assert m["value"] == "Sulfur Syntropy" and m["matched"]
    assert (m["dose"], m["frequency"]) == ("10 drops", "twice a day")
    # The spoken values win; timing, not spoken, comes from the catalogue.
    assert m["dosing"] == {"dosage": "10 drops", "frequency": "twice a day",
                           "timing": "with food, or as guided."}


def test_no_dose_spoken_takes_the_catalogue_default(cx):
    m = match_term(cx, "remedy", "sulfur syntropy")
    assert (m["dose"], m["frequency"]) == ("", "")
    assert m["dosing"] == {"dosage": "1 capsule", "frequency": "twice a day",
                           "timing": "with food, or as guided."}


@pytest.mark.parametrize("spoken,rest,dose,freq", [
    ("Liver Detox 10 drops twice a day", "Liver Detox", "10 drops", "twice a day"),
    ("two capsules three times a day of Sobopla", "Sobopla", "2 capsules", "three times a day"),
    ("Rose essence, 3 drops daily", "Rose essence", "3 drops", "daily"),
    ("half teaspoon at bedtime Alkalize", "Alkalize", "1/2 teaspoon", "at bedtime"),
    ("Sulfur Syntropy", "Sulfur Syntropy", "", ""),
])
def test_parse_dose(spoken, rest, dose, freq):
    assert parse_dose(spoken) == (rest, dose, freq)


# ---- head and tail ------------------------------------------------------------

def test_head_and_tail_before_the_stress_fills_both(cx):
    m = match_term(cx, "head", "head and tail liver congestion")
    assert m == {"value": "Liver Congestion", "matched": True, "also_tail": True}


def test_head_and_tail_after_the_stress_fills_both(cx):
    m = match_term(cx, "head", "Liver congestion, head and tail.")
    assert m == {"value": "Liver Congestion", "matched": True, "also_tail": True}
    # From the Tail mic too.
    assert match_term(cx, "tail", "liver congestion head and tail")["also_tail"] is True


def test_plain_head_fills_the_head_only(cx):
    m = match_term(cx, "head", "head liver congestion")
    assert m == {"value": "Liver Congestion", "matched": True, "also_tail": False}
    assert match_term(cx, "head", "liver congestion")["also_tail"] is False


def test_unknown_stress_is_kept_and_not_matched(cx):
    m = match_term(cx, "tail", "zzqx florpwhistle")
    assert m == {"value": "Zzqx Florpwhistle", "matched": False, "also_tail": False}


def test_this_clients_detected_stress_is_matched_too(cx):
    """resolve_stress_name knows only FileMaker's terms. The client's own detected
    stresses join the pool for a short recording."""
    from dashboard.biofield_stress import init_stress_tables
    init_stress_tables(cx)
    cx.execute("INSERT INTO biofield_auth_stress(test_id,code,label,source) "
               "VALUES(7,'X1','Glyphosate Burden','scan')")
    cx.commit()
    assert match_term(cx, "head", "glyphosate burdon", test_id="a7")["matched"] is True
    assert match_term(cx, "head", "glyphosate burdon", test_id="a7")["value"] == "Glyphosate Burden"
    # Another test's stress is not in this pool.
    assert match_term(cx, "head", "glyphosate burdon", test_id="a8")["matched"] is False


def test_bad_kind_is_refused(cx):
    with pytest.raises(ValueError):
        match_term(cx, "phase", "two")


# ---- balanced ------------------------------------------------------------------

@pytest.mark.parametrize("said", ["balanced", "Balanced.", "layer balanced",
                                  "this layer is balanced", "it's balanced"])
def test_saying_balanced_is_the_cue(cx, said):
    assert is_balanced_cue(said)
    for kind in ("head", "tail", "remedy"):
        assert match_term(cx, kind, said)["balanced"] is True


@pytest.mark.parametrize("said", ["unbalanced hormones", "balanced diet stress", "liver"])
def test_a_stress_with_the_word_in_it_is_not_the_cue(said):
    assert not is_balanced_cue(said)


# ---- the resolvers keep their old answers ---------------------------------------

def test_resolvers_return_values_are_unchanged(cx):
    assert resolve_remedy_name(cx, "sulfur centropy") == "Sulfur Syntropy"
    assert resolve_remedy_name(cx, "zzqx florpwhistle") == "Zzqx Florpwhistle"
    assert resolve_stress_name(cx, "liver congestoin") == "Liver Congestion"
    assert resolve_stress_name(cx, "zzqx florpwhistle") == "Zzqx Florpwhistle"
    assert resolve_remedy_match(cx, "sulfur centropy") == ("Sulfur Syntropy", True)
    assert resolve_stress_match(cx, "zzqx florpwhistle") == ("Zzqx Florpwhistle", False)
    assert resolve_remedy_match(cx, "") == ("", False)
