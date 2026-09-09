"""Pins the Fibrolysis line's catalog entries against a silent revert.

The 2026-06-06 reformulation split the old enzyme-led "Fibrolysis Factors" into
Fibrosolve (the enzymes) and a new botanical Fibrolysis Factors. The illtowell
product pages read data/products.json, and both entries were wrong there on
2026-09-08: Fibrosolve carried no ingredients at all, and Fibrolysis Factors
carried three zero-quantity FMP lines as if they were panel rows (one of them as
"(unnamed FMP ingredient 5583)") plus a placeholder description with no dosing.

Glen's ruling, 2026-09-09: a BOM line with zero quantity is a PROPOSED FUTURE
ENHANCEMENT and must not be listed publicly until it is implemented. That is why
they are excluded, and why none of their standardization percentages appears on
a dosed line.

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


# The zero-quantity lines behind these three products: proposed future enhancements
# recorded in FMP, none of which may appear on a page until it is implemented
# (Glen, 2026-09-09). Listed by the content that would give them away.
PROPOSED_NOT_SHIPPED = [
    "35,000",                 # fibrosolve raw 4259, nattokinase at a higher activity
    "70%",                    # fibrolysis-factors raw 4609, Salvia miltiorrhiza 70%
    "High AKBA",              # fibrolysis-factors raw 5583, boswellic acid 90%
    "90%",
    "50% glucoraphanin",      # estro-clear raw 4326
    "myrosinase",             # estro-clear raw 5608, red cabbage myrosinase
    "iodate",                 # estro-clear raw 5606, potassium iodate
    "unnamed FMP ingredient",
]


@pytest.mark.parametrize("slug", ["fibrosolve", "fibrolysis-factors", "estro-clear"])
def test_proposed_enhancements_never_reach_a_panel(products, slug):
    panel = " | ".join(f"{i['name']} {i['dose']}" for i in products[slug]["ingredients"])
    for token in PROPOSED_NOT_SHIPPED:
        assert token.lower() not in panel.lower(), f"{slug} publishes {token!r}"


# Estro-Clear is SKU 3, specced as "Estrolytic" and produced under this name. Its panel
# mixes mg with IU and mcg, so it pins row-for-row rather than by mass sum.
ESTRO_CLEAR = [
    ("Calcium-D-glucarate", "250 mg"),
    ("DIM 98% (diindolylmethane)", "100 mg"),
    ("Broccoli extract (Brassica oleracea), 17% glucoraphanin", "85 mg"),
    ("Vitamin C (L-ascorbic acid)", "50 mg"),
    ("Black pepper extract (95% piperine)", "5 mg"),
    ("Vitamin D3 (cholecalciferol)", "2,000 IU"),
    ("Black mustard seed (Brassica nigra)", "2.5 mg"),
    ("Iodine (from potassium iodide)", "150 mcg"),
    ("Selenium (as methylselenocysteine)", "100 mcg"),
]


def test_estro_clear_panel_matches_the_fmp_bom(products):
    p = products["estro-clear"]
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == ESTRO_CLEAR
    assert p["directions"].startswith("Take 1 capsule 1 to 2 times daily")
    assert "piperine" in p["warning"].lower()


def test_estro_clear_states_the_grade_actually_in_the_bottle(products):
    """The spec asks for 50% glucoraphanin and red cabbage myrosinase; FMP product
    1190 carries 17% and black mustard seed. The panel states the bottle, so these
    two strings must not quietly drift back to the spec's better materials."""
    names = [i["name"] for i in products["estro-clear"]["ingredients"]]
    assert any("17% glucoraphanin" in n for n in names)
    assert any("Brassica nigra" in n for n in names)
    assert not any("50% glucoraphanin" in n or "myrosinase" in n.lower() for n in names)


def test_no_estrolytic_sku_was_minted(products):
    """Specced as Estrolytic, produced as Estro-Clear. One SKU, not two."""
    assert "estrolytic" not in products
    assert not any((v.get("name") or "") == "Estrolytic" for v in products.values())


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_no_zero_quantity_rows(products, slug):
    """Zero-quantity BOM lines are proposed enhancements and never ship publicly."""
    for ing in products[slug]["ingredients"]:
        assert ing["dose"].strip(), f"{slug}: dose-less row {ing['name']!r}"
        assert "unnamed FMP ingredient" not in ing["name"]


def test_fibrolysis_factors_description_carries_dosing(products):
    """1 to 2 capsules, not FMP's 1: Glen's ruling 2026-09-08.

    The 2026-06-06 spec sets each per-capsule dose so that 2 capsules reach the
    active's minimum therapeutic dose (EGCG at the 400 mg trial floor). At 1
    capsule every active sits at half its minimum, so re-deriving this field
    from FMP product 356, which still reads "1 capsule" / "daily", under-doses
    the formula by its own design.
    """
    desc = products["fibrolysis-factors"]["description"]
    assert "Price: $69.97" not in desc, "placeholder description is back"
    assert "with food" in desc
    assert "1 to 2 capsules daily" in desc
    assert "1 capsule daily" not in desc, "reverted to the FMP extract's dosage field"


def test_fibrosolve_description_keeps_the_bleeding_warning(products):
    desc = products["fibrosolve"]["description"]
    for term in ("anticoagulant", "bleeding", "pregnancy", "surgery", "empty stomach"):
        assert term in desc.lower()


def test_estro_clear_correction_is_recorded(corrections):
    c = corrections["estro-clear"]
    assert [(i["name"], i["dose"]) for i in c["ingredients"]] == ESTRO_CLEAR
    assert c["ingredients_source"] == "fmp-bom-2026-08-31"


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_correction_is_recorded_so_enrichment_cannot_revert_it(corrections, slug):
    """products.json is the output; the corrections file is the source constant."""
    c = corrections[slug]
    assert [(i["name"], i["dose"]) for i in c["ingredients"]] == EXPECTED[slug]["ingredients"]
    assert c["ingredients_source"] == "fmp-bom-2026-08-31"


def test_apply_enrichment_replays_every_corrected_field():
    """A correction that apply_enrichment ignores is a fix waiting to revert."""
    src = open(os.path.join(ROOT, "scripts", "apply_enrichment.py")).read()
    block = src.split("Glen's manual corrections override")[1]
    for field in ("description", "bottle_type", "panel_note", "panel_note_link"):
        assert f'c.get("{field}")' in block, field
    assert '("directions", "warning")' in block


def test_glutathione_syntropy_pairing_is_on_the_page(products):
    """Glen, 2026-09-08: the spec's label line goes on the page, at the bottom.

    It sits under the ingredient list, which is where a label carries it. The
    thiol axis is deliberately in the companion product rather than in NAC here.
    """
    p = products["fibrolysis-factors"]
    assert p["panel_note"] == "Synergistic with Glutathione Syntropy."
    link = p["panel_note_link"]
    assert link["label"] in p["panel_note"]
    # the linked slug must be a real catalog product, not a fuzzy name match
    assert link["url"] == "/begin/product/glutathione-syntropy"
    assert products["glutathione-syntropy"]["name"] == link["label"]


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
    # the pairing note is served with the panel, not buried in the prose sections
    ff = c.get("/begin/product-page-data/fibrolysis-factors").get_json()
    body = next(s for s in ff["sections"] if s["id"] == "ingredients")["body"]
    assert body["note"] == "Synergistic with Glutathione Syntropy."
    assert body["note_link"]["url"] == "/begin/product/glutathione-syntropy"
    fs = c.get("/begin/product-page-data/fibrosolve").get_json()
    fsb = next(s for s in fs["sections"] if s["id"] == "ingredients")["body"]
    assert not fsb["note"], "note must not leak onto products that have none"
    # Dosing and the contraindication must survive the AI draft, which replaces the
    # description section wholesale. Serving them here is what makes that true.
    assert body["directions"] == "Take 1 to 2 capsules daily with food."
    assert not body["warning"]
    assert fsb["directions"].startswith("Take 1 capsule 1 to 2 times daily")
    for term in ("anticoagulant", "bleeding", "pregnancy", "surgery"):
        assert term in fsb["warning"].lower(), term


def test_label_lines_are_not_left_to_the_generated_copy(products):
    """The live page carried no dosing on 2026-09-09 because the cached AI draft
    replaces the description section wholesale. products.json description is the
    generator's input, not what the reader sees, so a dose and a contraindication
    have to be their own fields."""
    ff = products["fibrolysis-factors"]
    assert ff["directions"] == "Take 1 to 2 capsules daily with food."
    fs = products["fibrosolve"]
    assert "empty stomach" in fs["directions"]
    assert "anticoagulant" in fs["warning"].lower()
    src = open(os.path.join(ROOT, "static", "begin-product.html")).read()
    for token in ("body.directions", "body.warning", "Suggested use: ", "Caution: "):
        assert token in src, token
