"""Curated remedy substitution on the reveal client read path: an unpurchasable
matched remedy ("Relax", 404 on /begin/buy/relax) is swapped for its sellable
equivalent ("Stress Release", /begin/buy/stress-release resolves) everywhere it
renders with an order button — remedies list AND layer remedies, so display,
entitlement, pricing, and checkout stay in sync.
"""
from dashboard.biofield_reveals import apply_remedy_substitutions


def test_relax_slug_swapped_to_stress_release():
    row = {"remedies": [{"name": "Relax", "slug": "relax", "meaning": "old"}]}
    apply_remedy_substitutions(row)
    r = row["remedies"][0]
    assert r["slug"] == "stress-release"
    assert r["name"] == "Stress Release"
    assert "Stress Release" in r["meaning"]  # meaning swapped, not left describing Relax


def test_relax_by_name_when_slug_differs():
    row = {"remedies": [{"name": "Relax", "slug": "", "meaning": "x"}]}
    apply_remedy_substitutions(row)
    assert row["remedies"][0]["slug"] == "stress-release"


def test_layer_remedy_swapped_too():
    row = {"remedies": [], "layers": [
        {"n": 3, "title": "Calm", "remedy": {"name": "Relax", "slug": "relax"}},
        {"n": 4, "title": "Liver", "remedy": {"name": "Liver Support", "slug": "liver-support"}},
    ]}
    apply_remedy_substitutions(row)
    assert row["layers"][0]["remedy"]["slug"] == "stress-release"
    assert row["layers"][1]["remedy"]["slug"] == "liver-support"  # untouched


def test_non_matching_untouched():
    row = {"remedies": [{"name": "Heart Health", "slug": "heart-health"}]}
    apply_remedy_substitutions(row)
    assert row["remedies"][0]["slug"] == "heart-health"


def test_idempotent():
    row = {"remedies": [{"name": "Relax", "slug": "relax"}]}
    apply_remedy_substitutions(row)
    apply_remedy_substitutions(row)  # second pass: stress-release is not a key
    assert row["remedies"][0]["slug"] == "stress-release"


def test_safe_on_none_and_malformed():
    assert apply_remedy_substitutions(None) is None
    # missing keys / non-dict remedies must not raise
    apply_remedy_substitutions({"remedies": [None, "x", {}], "layers": [None, {"remedy": None}]})


# --- Preference substitutions (Glen 2026-07-19): recommend Y rather than X ---

def test_allerfree_swapped_to_immune_modulation():
    row = {"remedies": [{"name": "AllerFree", "slug": "allerfree", "meaning": "old"}]}
    apply_remedy_substitutions(row)
    r = row["remedies"][0]
    assert r["slug"] == "immune-modulation"   # sellable at /begin/buy/immune-modulation ($69.97)
    assert r["name"] == "Immune Modulation"
    assert "Immune Modulation" in r["meaning"]  # meaning swapped, not left describing AllerFree
    assert "allerfree" not in r["meaning"].lower()


def test_allerfree_by_full_drops_name():
    # matched row may carry the fuller FMP name with an empty/different slug
    row = {"remedies": [{"name": "AllerFree HomeoEnergetic Drops", "slug": ""}]}
    apply_remedy_substitutions(row)
    assert row["remedies"][0]["slug"] == "immune-modulation"


def test_bone_builder_swapped_to_neuro_magnesium():
    row = {"remedies": [{"name": "Bone Builder", "slug": "bone-builder", "meaning": "old"}]}
    apply_remedy_substitutions(row)
    r = row["remedies"][0]
    assert r["slug"] == "neuro-magnesium"   # sellable at /begin/buy/neuro-magnesium ($69.97)
    assert r["name"] == "Neuro-Magnesium"
    assert "Neuro-Magnesium" in r["meaning"]
    assert "bone builder" not in r["meaning"].lower()


def test_bone_builder_by_name_when_slug_differs():
    row = {"remedies": [{"name": "Bone Builder", "slug": "13-bone-builder"}]}
    apply_remedy_substitutions(row)
    assert row["remedies"][0]["slug"] == "neuro-magnesium"


def test_preference_swaps_in_layers_and_idempotent():
    row = {"remedies": [], "layers": [
        {"n": 2, "title": "Immune", "remedy": {"name": "AllerFree", "slug": "allerfree"}},
        {"n": 5, "title": "Bone", "remedy": {"name": "Bone Builder", "slug": "bone-builder"}},
    ]}
    apply_remedy_substitutions(row)
    apply_remedy_substitutions(row)  # second pass: swapped-in slugs are not keys
    assert row["layers"][0]["remedy"]["slug"] == "immune-modulation"
    assert row["layers"][1]["remedy"]["slug"] == "neuro-magnesium"


# --- Aller-Free is the correct spelling of AllerFree, same formula (Glen 2026-09-24) ---
# The live catalog sells "Aller-Free Aid for Inhalant Allergies" (slug aller-free-aid).
# The keys above only matched the unhyphenated spelling, so 15 draft reveals carried it.

import pytest


@pytest.mark.parametrize("name,slug", [
    ("Aller-Free Aid for Inhalant Allergies", "aller-free-aid"),
    ("Aller-Free Aid for Inhalant Allergies", ""),          # by name alone
    ("", "aller-free-aid"),                                  # by slug alone
    ("AllerFree HomeoEnergetic Drops", "allerfree-homeoenergetic-drops"),
    ("Aller Free", ""),                                      # spaced spelling
    ("ALLER-FREE", ""),                                      # case
])
def test_every_aller_free_spelling_swaps_to_immune_modulation(name, slug):
    row = {"remedies": [{"name": name, "slug": slug, "meaning": "old"}],
           "layers": [{"n": 1, "remedy": {"name": name, "slug": slug}}]}
    apply_remedy_substitutions(row)
    for r in (row["remedies"][0], row["layers"][0]["remedy"]):
        assert r["slug"] == "immune-modulation"
        assert r["name"] == "Immune Modulation"


@pytest.mark.parametrize("name,slug", [
    ("Allergen II Homeopathic Complex in Terrain Restore", ""),
    ("Allermet Homeopathic Complex in Terrain Restore", ""),
    ("Immune Modulation", "immune-modulation"),
    ("Sugar-Free Syrup", ""),
])
def test_other_allergy_and_free_products_untouched(name, slug):
    row = {"remedies": [{"name": name, "slug": slug}]}
    apply_remedy_substitutions(row)
    assert row["remedies"][0]["name"] == name


def test_is_aller_free_helper_matches_spellings_and_nothing_else():
    from dashboard.biofield_reveals import is_aller_free
    for t in ("Aller-Free Aid for Inhalant Allergies", "aller-free-aid", "AllerFree",
              "aller free", "allerfree-homeoenergetic-drops"):
        assert is_aller_free(t), t
    for t in ("Allergen II Homeopathic Complex", "Immune Modulation", "Sugar-Free", "", None):
        assert not is_aller_free(t), t


def test_swap_drops_the_old_products_dosing():
    # Live draft, 2026-09-24: Aller-Free with drops dosing must not keep it as Immune Modulation.
    row = {"layers": [{"n": 2, "remedy": {"name": "Aller-Free Aid for Inhalant Allergies",
                                          "slug": "aller-free-aid",
                                          "dosing": "10 drops 3 times a day or as needed"}}]}
    apply_remedy_substitutions(row)
    rem = row["layers"][0]["remedy"]
    assert rem["name"] == "Immune Modulation"
    assert "drops" not in (rem.get("dosing") or "")


def test_is_aller_free_needs_a_word_start():
    from dashboard.biofield_reveals import is_aller_free
    assert not is_aller_free("Smaller Free Range Eggs")
