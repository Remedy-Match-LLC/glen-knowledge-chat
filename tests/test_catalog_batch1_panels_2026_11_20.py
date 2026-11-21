"""Heart Health, Perfect Skin and Glucose Tolerance follow the store-page specs Glen
approved on 2026-11-20 in the production tab.

Specs (vault, production pillar):
  production/05 Formulations/heart-health/2026-09-27/heart-health-store-page.md
  production/05 Formulations/perfect-skin/2026-09-27/perfect-skin-store-page.md
  production/05 Formulations/glucose-tolerance/2026-10-24/glucose-tolerance-store-page.md

Each spec's "Proposed page text" ingredient list is the panel. Its "Caution section"
quote is the warning. The old panels held 24, 17 and 9 rows. Heart Health and Perfect
Skin warned about vinpocetine, which neither formula contains now.
"""
import copy
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "data"
PRODUCTS = json.loads((DATA / "products.json").read_text())["products"]
CORRECTIONS = json.loads((DATA / "products-manual-corrections.json").read_text())

DV = "of the Daily Value"
PANELS = {
    "heart-health": [
        ("Magnesium (as dimagnesium malate)", f"49 mg, 12% {DV}"),
        ("Selenium (as methylselenocysteine)", f"67 mcg, 122% {DV}"),
        ("Coenzyme Q10 98% (ubiquinone)", "102 mg"),
        ("Hawthorn flavonoids 85% (Crataegus oxyacantha)", "170 mg"),
    ],
    "perfect-skin": [
        ("Golden serpent fern 30:1 (Polypodium leucotomos)", "240 mg"),
        ("Gotu kola TECA: asiaticosides 80% (Centella asiatica) (herb)", "100 mg"),
        ("Pine bark OPCs 95% (oligomeric proanthocyanidins) (Pinus pinaster) (bark)", "50 mg"),
        ("Rice phytoceramides 10% (Oryza sativa)", "46 mg"),
        ("Hyaluronic acid, high molecular weight, vegan source", "30 mg"),
        ("Astaxanthin 96% (Haematococcus pluvialis)", "2.1 mg"),
    ],
    "glucose-tolerance": [
        ("Dihydroberberine 99% (Phellodendron chinense)", "100 mg"),
        ("French lilac 20:1 (Galega officinalis)", "100 mg"),
        ("Benfotiamine 99%", f"125 mg (about 70 mg thiamine, about 5,900% {DV})"),
        ("Chromium (as chromium polynicotinate)", f"200 mcg, 571% {DV}"),
        ("Stabilized Bio-Enhanced® Na R-lipoate", "10 mg"),
        ("Pterostilbene 98%", "50 mg"),
        ("Bitter melon charantin 10% (Momordica charantia)", "50 mg"),
        ("Banaba corosolic acid 20% (Lagerstroemia speciosa)", "50 mg"),
    ],
}

SPEC_DIRS = {
    "heart-health": "production/05 Formulations/heart-health/2026-09-27",
    "perfect-skin": "production/05 Formulations/perfect-skin/2026-09-27",
    "glucose-tolerance": "production/05 Formulations/glucose-tolerance/2026-10-24",
}

# Key clauses of each spec's caution quote.
CAUTION_CLAUSES = {
    "heart-health": [
        "pregnant or breastfeeding",
        "prescription medications",
        "warfarin or other blood thinners, digoxin, nitrates and blood pressure medicines",
        "thyroid medicine and some antibiotics",
        "kidney disease",
        "selenium from all sources under 400 mcg a day",
    ],
    "perfect-skin": [
        "pregnant or breastfeeding",
        "prescription medications",
        "blood thinners, immune-suppressing drugs, sedatives and blood sugar medicines",
        "liver disease or a bleeding disorder",
        "Stop 2 weeks before planned surgery",
        "allergic to ferns or pine",
    ],
    "glucose-tolerance": [
        "pregnant or breastfeeding",
        "not for children",
        "blood sugar, blood pressure or thyroid medicines",
        "prone to low blood sugar",
        "kidney or liver disease",
        "G6PD deficiency",
        "Stop 2 weeks before planned surgery",
        "allergic to legumes or gourds",
        "may lower blood sugar and change how some medications are processed",
    ],
}

# Ingredients the old panels carried that the approved formulas do not.
DROPPED = {
    "heart-health": [
        "vinpocetine", "vinca", "vitamin c", "ascorbate", "vitamin d3", "cholecalciferol",
        "vitamin e", "tocopherol", "dhea", "lumbrokinase", "curcumin", "inosine",
        "modified citrus pectin", "salvianolic", "astaxanthin", "reishi", "ganoderma",
        "catechins", "camelia", "humic", "ginkgo", "egb 761", "niacin", "moringa",
        "licorice", "gingerol", "piperine", "taurate", "ultra absorption",
    ],
    "perfect-skin": [
        "vinpocetine", "biotin", "bamboo", "silica", "polygon", "he shou wu", "fo-ti",
        "gypenosides", "jiaogulan", "bioavailability", "orgono", "green tea", "egcg",
        "pterostilbene", "pomegranate", "punicalagin", "lychee", "litchi", "konjac",
    ],
    "glucose-tolerance": [
        "gymnem", "vinpocetine", "metformin",
    ],
}


def _rows(entry):
    return [(i["name"], i["dose"]) for i in entry["ingredients"]]


def _text(entry):
    """Every customer-facing string in the entry: panel, description, intro, warning."""
    parts = [f"{n} {d}" for n, d in _rows(entry)]
    for key in ("description", "intro", "warning", "directions", "panel_note"):
        if entry.get(key):
            parts.append(entry[key])
    return " ".join(parts).lower()


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_panel_names_and_amounts_match_the_spec_in_live_and_pinned_copies(slug):
    for entry in (PRODUCTS[slug], CORRECTIONS[slug]):
        assert _rows(entry) == PANELS[slug]


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_panel_is_pinned_so_a_filemaker_rebuild_skips_it(slug):
    from scripts.generate_panels_from_db import _manual_slugs
    assert slug in _manual_slugs(str(DATA / "products.json"))
    src = CORRECTIONS[slug]["ingredients_source"]
    assert src == PRODUCTS[slug]["ingredients_source"]
    assert SPEC_DIRS[slug] in src and "2026-11-20" in src


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_warning_is_the_spec_caution_and_drops_vinpocetine(slug):
    live, pin = PRODUCTS[slug], CORRECTIONS[slug]
    assert live["warning"] == pin["warning"]
    assert "vinpocetine" not in live["warning"].lower()
    for clause in CAUTION_CLAUSES[slug]:
        assert clause in live["warning"], clause
    assert live["chat_caution"] is True and pin["chat_caution"] is True


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_dropped_ingredients_are_gone_from_the_entry(slug):
    for entry in (PRODUCTS[slug], CORRECTIONS[slug]):
        text = _text(entry)
        for name in DROPPED[slug]:
            assert name not in text, (slug, name)


# Copied word for word from each spec's "Proposed page text" and "Caution section"
# (production/05 Formulations/<slug>/<date>/<slug>-store-page.md), Glen approved 2026-11-20.
# Whole-string equality, so an inverted or trimmed caution fails (review round 2).
SPEC_WARNING = {
    "heart-health": ("Don't use this if you're pregnant or breastfeeding. Consult your health "
        "professional before use if you take prescription medications. This matters most with "
        "warfarin or other blood thinners, digoxin, nitrates and blood pressure medicines. It "
        "also applies to thyroid medicine and some antibiotics, so take those apart from this. "
        "Also consult first if you have kidney disease. Keep your total selenium from all "
        "sources under 400 mcg a day."),
    "perfect-skin": ("Don't use this if you're pregnant or breastfeeding. Consult your health "
        "professional before use if you take prescription medications. This matters most with "
        "blood thinners, immune-suppressing drugs, sedatives and blood sugar medicines. Also "
        "consult first if you have liver disease or a bleeding disorder. Stop 2 weeks before "
        "planned surgery. Ask first too if you are allergic to ferns or pine."),
    "glucose-tolerance": ("Do not use if you are pregnant or breastfeeding. It is not for "
        "children. Consult your health professional before use if you take prescription "
        "medications, especially blood sugar, blood pressure or thyroid medicines. Also consult "
        "first if you are prone to low blood sugar, or have kidney or liver disease. Avoid it "
        "with G6PD deficiency. Stop 2 weeks before planned surgery. Ask first too if you are "
        "allergic to legumes or gourds. This formula may lower blood sugar and change how some "
        "medications are processed."),
}
SPEC_DESCRIPTION = {
    "heart-health": ("Your heart never clocks out. It beats around 100,000 times a day, awake or "
        "asleep. Every beat takes energy. Your cells build that energy fresh from food and "
        "oxygen, nonstop. Coenzyme Q10 and magnesium help keep that energy work running. "
        "Selenium supports the antioxidant enzymes that protect heart cells.\n\n"
        "It can be taken alongside Rhythm Restore."),
    "perfect-skin": ("Support your skin's barrier as it holds onto moisture, for a healthier and "
        "more radiant look.\n\nYour skin is your largest organ. Its outer layer locks water in "
        "with ceramides, the fats between skin cells. Beneath it, the skin's own collagen and "
        "hyaluronic acid keep it firm and supple."),
    "glucose-tolerance": ("Chromium helps insulin carry glucose into your cells. Refining grains "
        "and sugar strips out much of the chromium in whole foods.\n\nThis formula supplies "
        "chromium as chromium polynicotinate, often sold as GTF chromium. Poly means many. "
        "Nicotinate refers to vitamin B3, also known as niacin or nicotinic acid."),
}
SPEC_DIRECTIONS = {
    "heart-health": "1 to 3 capsules daily with a meal containing fat, or as guided.",
    "perfect-skin": "1 to 2 capsules daily with food, or as guided.",
    "glucose-tolerance": ("Take 1 capsule daily with food, or with cravings for sugar or "
                          "stimulants, or as guided."),
}


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_the_warning_is_the_whole_spec_caution(slug):
    for entry in (PRODUCTS[slug], CORRECTIONS[slug]):
        assert entry["warning"] == SPEC_WARNING[slug]


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_the_page_text_is_the_spec_and_pinned_against_ai_drafts(slug):
    # Round 1 review: without copy_pinned an AI draft could replace the approved text.
    for entry in (PRODUCTS[slug], CORRECTIONS[slug]):
        assert entry["description"] == SPEC_DESCRIPTION[slug]
        assert entry["directions"] == SPEC_DIRECTIONS[slug]
        assert entry["directions"].endswith(", or as guided.")
        assert set(entry["copy_pinned"]) == {"ingredients", "directions", "description"}
    assert "5 years" not in PRODUCTS["glucose-tolerance"]["description"]


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_old_generated_drafts_are_reset(slug, monkeypatch):
    assert PRODUCTS[slug]["content_since"] == CORRECTIONS[slug]["content_since"] >= "2026-11-20"
    # Round 2 review: drive the cache reader. A draft from before the stamp is regenerated;
    # one from after it is served as is.
    from dashboard import product_content as pc
    product = dict(PRODUCTS[slug], slug=slug)
    since = product["content_since"]
    made = []
    monkeypatch.setattr(pc, "_page_text_from_product", lambda p: {"text": "x", "url": ""})
    monkeypatch.setattr(pc, "_page_text", lambda p: None)
    monkeypatch.setattr(pc, "_generate_card", lambda p, page: made.append(1) or "NEW")
    monkeypatch.setattr(pc, "_cache_put", lambda *a, **k: None)
    monkeypatch.setattr(pc, "_cache_get", lambda s, t: {
        "content": "OLD vinpocetine", "sources": [], "generated_at": "2026-11-01T00:00:00+00:00"})
    assert pc.get_or_generate(product, "card")["content"] == "NEW" and made == [1]
    monkeypatch.setattr(pc, "_cache_get", lambda s, t: {
        "content": "FRESH", "sources": [], "generated_at": since[:4] + "-12-31T00:00:00+00:00"})
    assert pc.get_or_generate(product, "card")["content"] == "FRESH" and made == [1]


@pytest.mark.parametrize("slug", sorted(PANELS))
def test_a_replay_of_the_corrections_restores_the_approved_entry(slug):
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    stale = products[slug]
    stale["ingredients"] = [{"name": "Vinpocetine (Vinca minor)", "dose": "1 mg"}]
    stale["warning"] = "This formula contains vinpocetine."
    if "description" in stale:
        stale["description"] = "old"
    stale.pop("content_since", None)
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[slug] == PRODUCTS[slug]
