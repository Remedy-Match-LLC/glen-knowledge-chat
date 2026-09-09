"""Pins the Fibrolysis line's catalog entries against a silent revert.

The 2026-06-06 reformulation split the old enzyme-led "Fibrolysis Factors" into
Fibrosolve (the enzymes) and a new botanical Fibrolysis Factors. The illtowell
product pages read data/products.json, and both entries were wrong there on
2026-09-08: Fibrosolve carried no ingredients at all, and Fibrolysis Factors
carried three zero-quantity FMP marker lines as if they were panel rows (one of
them as "(unnamed FMP ingredient 5583)") plus a placeholder description with no
dosing.

Both fixes also live in data/products-manual-corrections.json, which is what
scripts/apply_enrichment.py replays. Pinning products.json alone would let the
next enrichment run revert the page while this file still passed.

Masses are from FMP BOM extract 2026-08-31 and must sum to that extract's
zc_total_mg (490 and 487.5), which is what proves the zero-quantity lines carry
no mass and so do not belong on the panel.
"""
import importlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(ROOT, "data", "products.json")
CORRECTIONS = os.path.join(ROOT, "data", "products-manual-corrections.json")

EXPECTED = {
    "fibrosolve": {
        "total_mg": 490.0,
        "ingredients": [
            ("Lumbrokinase", "15 mg"),
            ("Nattokinase (Bacillus subtilis)", "100 mg"),
            ("Serrapeptase", "25 mg"),
            ("Bromelain", "350 mg"),
        ],
    },
    "fibrolysis-factors": {
        "total_mg": 487.5,
        "ingredients": [
            ("Frankincense (Boswellia serrata)", "50 mg"),
            ("Green Tea Extract, Organic (98% Polyphenols / 50% EGCG)", "200 mg"),
            ("Salvianolic Acid B 20% (Salvia miltiorrhiza)", "125 mg"),
            ("Trans-Resveratrol", "75 mg"),
            ("Gotu kola (Centella asiatica)", "37.5 mg"),
        ],
    },
}


def _mg(dose):
    return float(re.match(r"([\d.]+)\s*mg$", dose).group(1))


@pytest.fixture(scope="module")
def products():
    return json.load(open(PRODUCTS))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open(CORRECTIONS))


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_panel_matches_the_fmp_bom(products, slug):
    got = [(i["name"], i["dose"]) for i in products[slug]["ingredients"]]
    assert got == EXPECTED[slug]["ingredients"]


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_panel_sums_to_the_capsule_total(products, slug):
    total = sum(_mg(i["dose"]) for i in products[slug]["ingredients"])
    assert total == pytest.approx(EXPECTED[slug]["total_mg"])


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_no_zero_quantity_marker_rows(products, slug):
    """The three FMP marker lines and the unresolved-name placeholder are gone."""
    for ing in products[slug]["ingredients"]:
        assert ing["dose"].strip(), f"{slug}: dose-less row {ing['name']!r}"
        assert "unnamed FMP ingredient" not in ing["name"]


def test_fibrolysis_factors_description_carries_dosing(products):
    desc = products["fibrolysis-factors"]["description"]
    assert "Price: $69.97" not in desc, "placeholder description is back"
    assert "with food" in desc
    assert "1 capsule daily" in desc


def test_fibrosolve_description_keeps_the_bleeding_warning(products):
    desc = products["fibrosolve"]["description"]
    for term in ("anticoagulant", "bleeding", "pregnancy", "surgery", "empty stomach"):
        assert term in desc.lower()


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_correction_is_recorded_so_enrichment_cannot_revert_it(corrections, slug):
    """products.json is the output; the corrections file is the source constant."""
    c = corrections[slug]
    assert [(i["name"], i["dose"]) for i in c["ingredients"]] == EXPECTED[slug]["ingredients"]
    assert c["ingredients_source"] == "fmp-bom-2026-08-31"


def test_apply_enrichment_replays_description_and_bottle_type():
    """A correction that apply_enrichment ignores is a fix waiting to revert."""
    src = open(os.path.join(ROOT, "scripts", "apply_enrichment.py")).read()
    block = src.split("Glen's manual corrections override")[1]
    assert 'c.get("description")' in block
    assert 'c.get("bottle_type")' in block


def test_page_data_serves_the_panel(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    # app.py refuses to import without these; the test never calls either service.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    for slug in sorted(EXPECTED):
        data = c.get(f"/begin/product-page-data/{slug}").get_json()
        sec = next(s for s in data["sections"] if s["id"] == "ingredients")
        got = [(i["name"], i["dose"]) for i in sec["body"]["ingredients"]]
        assert got == EXPECTED[slug]["ingredients"], slug
