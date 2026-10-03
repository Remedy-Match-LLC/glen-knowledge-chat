"""Day, EMF and Vagus: one listing each (production spec 2026-09-20; EMF on Glen's word
2026-10-03). Applied by scripts/merge_infoceutical_twins.py from
tests/fixtures/catalog-merges-day-vagus-emf-2026-10-03.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _p():
    return json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["products"]


def test_the_twins_are_retired_into_their_survivors():
    P = _p()
    for twin, surv in (("day", "day-infoceutical"), ("emf", "emf-infoceutical"),
                       ("vagus-nerve-support-drops", "vagus-nerve-support")):
        assert P[twin].get("inactive") is True and P[twin]["superseded_by"] == surv, twin
        assert not P[surv].get("inactive"), surv
        assert not P[twin].get("fmp_id"), twin


def test_the_survivors_carry_the_filemaker_ids():
    P = _p()
    assert P["day-infoceutical"]["fmp_id"] == "196"
    assert P["emf-infoceutical"]["fmp_id"] == "270"
    assert P["vagus-nerve-support"]["fmp_id"] == "460"


def test_vagus_has_glens_directions_and_no_struck_price():
    v = _p()["vagus-nerve-support"]
    assert v["directions"] == "10 drops up to 3 times a day before meals."
    assert "answered the directions on 2026-09-30" in v["enrichment_note"]
    assert not v.get("regular_cents")
    assert v["price_cents"] == 6997 and v["bottle_type"] == "Dropper 50 mL"
    assert len(v["ingredients"]) == 24


def test_the_vitamin_list_stays_behind_and_the_kit_is_untouched():
    P = _p()
    assert not P["day-infoceutical"].get("ingredients")
    assert not P["vagus-nerve-stimulation-kit"].get("inactive")


def test_old_slugs_follow_to_the_survivor():
    from dashboard.products import superseded_slug
    P = _p()
    assert superseded_slug("day", P) == "day-infoceutical"
    assert superseded_slug("emf", P) == "emf-infoceutical"
    assert superseded_slug("vagus-nerve-support-drops", P) == "vagus-nerve-support"


def test_related_products_moved_to_the_survivors():
    d = json.loads((ROOT / "data" / "related-harvested.json").read_text(encoding="utf-8"))
    assert "day" not in d and "emf" not in d
    assert d["day-infoceutical"] and d["emf-infoceutical"]


def test_no_infoceutical_survivor_shows_a_struck_price():
    """Production 2026-10-03: no ruling set regular_cents on any of them; 4000 against
    3997 showed a struck $40.00 next to $39.97."""
    P = _p()
    rows = json.loads((ROOT / "tests" / "fixtures" / "infoceutical-merge-2026-10-03.json")
                      .read_text(encoding="utf-8"))
    survivors = [r["survivor"] for r in rows] + ["day-infoceutical", "emf-infoceutical"]
    assert not [s for s in survivors if P[s].get("regular_cents")]
