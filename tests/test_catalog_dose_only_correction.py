"""Catalogue corrections for Lymph Flow (October formula) and Bone Builder D3, 2026-11-23."""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from apply_enrichment import apply_corrections  # noqa: E402

CORR = json.load(open("data/products-manual-corrections.json"))
PRODUCTS = json.load(open("data/products.json"))["products"]
D3 = "Vitamin D3: Cholecalciferol"


def _dose(p, name):
    return [i["dose"] for i in p["ingredients"] if i["name"] == name]


def test_bone_builder_shows_250_iu_of_d3():
    assert _dose(PRODUCTS["bone-builder"], D3) == ["6.25 mcg (250 IU)"]


def test_a_dose_only_fix_leaves_the_other_lines_and_their_source_to_upstream():
    upstream = copy.deepcopy(PRODUCTS["bone-builder"])
    upstream["ingredients_source"] = "db-formulations-later"
    upstream["ingredients"].append({"name": "A later upstream line", "dose": "1 mg"})
    upstream["gk_stale"] = True
    apply_corrections({"bone-builder": upstream}, {"bone-builder": CORR["bone-builder"]})
    assert _dose(upstream, D3) == ["6.25 mcg (250 IU)"]
    assert _dose(upstream, "A later upstream line") == ["1 mg"]
    assert upstream["ingredients_source"] == "db-formulations-later"
    assert upstream["gk_stale"] is True


def test_a_dose_fix_naming_a_missing_line_fails_loudly():
    p = {"ingredients": [{"name": "Other", "dose": "1 mg"}]}
    with pytest.raises(ValueError):
        apply_corrections({"x": p}, {"x": {"ingredient_doses": {D3: "6.25 mcg"}}})


def test_lymph_flow_is_the_october_formula():
    p = PRODUCTS["lymph-flow"]
    assert [(i["name"].split(" (")[0], i["dose"]) for i in p["ingredients"]] == [
        ("Butcher's broom extract", "150 mg"),
        ("Hesperidin methyl chalcone 98%", "150 mg"),
        ("Gotu kola TECA", "75 mg"),
        ("Red root 4:1", "25 mg"),
        ("Vitamin C", "100 mg"),
        ("Selenium", "100 mcg"),
    ]
    assert p["directions"] == "Take 1 vegicap with food, up to 2 times a day, or as guided."
    text = json.dumps(p).lower()
    for old in ("nac", "glutathione", "phytolacca", "galium", "poke"):
        assert old not in text
    assert p["ingredients_source"] == "manual"


def test_the_catalogue_is_what_the_replay_produces():
    for slug in ("lymph-flow", "bone-builder"):
        p = copy.deepcopy(PRODUCTS[slug])
        apply_corrections({slug: p}, {slug: CORR[slug]})
        assert p == PRODUCTS[slug]


def test_the_dose_fix_survives_a_rebuild_from_the_enrichment_file():
    clean = json.load(open("data/products-enrich-clean.json"))["bone-builder"]
    p = {"ingredients": copy.deepcopy(clean["ingredients"]), "ingredients_source": "fmp_new"}
    apply_corrections({"bone-builder": p}, {"bone-builder": CORR["bone-builder"]})
    d3 = [i["dose"] for i in p["ingredients"] if "cholecalciferol" in i["name"].lower()]
    assert d3 == ["6.25 mcg (250 IU)"]
    assert p["ingredients_source"] == "fmp_new"


def test_lymph_flow_page_shows_only_approved_sections():
    p = PRODUCTS["lymph-flow"]
    assert set(p["copy_pinned"]) >= {"intro", "description", "directions", "research", "ingredients"}
    assert p["content_since"] >= "2026-11-24"
    assert [l["url"] for l in p["research_links"]] == [
        "https://pubmed.ncbi.nlm.nih.gov/8721977/", "https://pubmed.ncbi.nlm.nih.gov/3544968/"]
