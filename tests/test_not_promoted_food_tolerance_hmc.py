"""Food Tolerance and HMC: available, not promoted (Glen, 2026-10-03).

"Immune Modulation can take the place of HMC, AllerFree, and Food Tolerance.
They are available, but not promoted." Food Tolerance stays live and sellable.
HMC is an ingredient, not a product: a formula containing it is never suppressed."""
import json
import os

from dashboard import biofield_reveals as R
from dashboard import related_products as RP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_spellings_match():
    for t in ("Food Tolerance", "Food-Tolerance", "FoodTolerance",
              "Food Tolerance HomeoEnergetic Drops", "food-tolerance"):
        assert R.is_food_tolerance(t), t
    for t in ("HMC", "Hesperidin Methyl Chalcone", "hesperidin-methyl-chalcone"):
        assert R.is_hmc(t), t


def test_a_formula_containing_hmc_is_not_hmc():
    for t in ("Vitreous Vitality", "Hesperidin", "Macular Wellness Crocin", "Seafood tolerant"):
        assert not R.is_hmc(t), t
    assert not R.immune_modulation_instead("Vitreous Vitality")


def test_reveals_swap_both_for_immune_modulation():
    for rem in ({"name": "Food Tolerance", "slug": "food-tolerance"},
                {"name": "Food Tolerance HomeoEnergetic Drops",
                 "slug": "food-tolerance-homeoenergetic-drops"},
                {"name": "HMC", "slug": ""}):
        assert R._sub_for(rem)["slug"] == "immune-modulation", rem
    assert R._sub_for({"name": "Vitreous Vitality", "slug": "vitreous-vitality"}) is None


def test_related_products_never_list_food_tolerance():
    assert {"food-tolerance", "food-tolerance-homeoenergetic-drops"} <= RP.DO_NOT_RECOMMEND


def test_food_tolerance_stays_sellable():
    P = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    for s in ("food-tolerance", "food-tolerance-homeoenergetic-drops"):
        assert s in P and not P[s].get("inactive"), s


def test_the_matcher_excludes_both_and_keeps_formulas_with_hmc():
    import importlib
    import app as a
    importlib.reload(a)
    assert a._ff_auto_excluded("Food Tolerance")
    assert a._ff_auto_excluded("Food-Tolerance HomeoEnergetic Drops")
    assert a._ff_auto_excluded("HMC")
    assert not a._ff_auto_excluded("Vitreous Vitality")
    assert not a._ff_auto_excluded("Immune Modulation")
