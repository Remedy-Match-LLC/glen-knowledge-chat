"""B17 Syntropy's warning and dose (Glen, 2026-10-25, "yes" to each, formulation tab).

Source: production/05 Formulations/b17-syntropy/2026-10-25/b17-syntropy-warning-and-dose.md.
Each capsule of run 188 holds about 57,600 IU of vitamin A, so the only dose is one a day.
The old store copy said to decrease the dose if lightheaded, an early sign of cyanide
toxicity; the warning says to stop and call Poison Control. Glen approved the
pregnancy sentence's wording in the marketing tab the same day.
"""
import json
import os

import pytest

import app

SLUG = "b17-syntropy"
DIRECTIONS = "1 capsule a day with food, or as guided."
# Glen added three vitamin A sentences, 2026-10-25 (b17-syntropy-warning-and-dose.md).
WARNING = "Amygdalin can release cyanide in the body. Stop at the first sign of headache, dizziness, lightheadedness, weakness, nausea, vomiting, confusion, chest tightness or unusual breathing, and call Poison Control, 1-800-222-1222. Call 911 for breathing difficulty, fainting, seizure, bluish lips or loss of consciousness. Do not combine with apricot kernels, bitter almonds, fruit pits, beta-glucosidase or high-dose vitamin C. Do not use it in pregnancy, if you may become pregnant, or while breastfeeding, or give it to children. Consult a physician and pharmacist before use with any medication or medical treatment, especially diabetes medicines or blood thinners. Consult first if you have liver disease or drink alcohol regularly. Do not take other vitamin A supplements with this product. Use for longer than 3 months only under a practitioner's guidance. Keep out of reach of children."

# The approved panel, run 188 (b17-syntropy-store-page.md, "Proposed page text").
PANEL = [
    {"name": "Vitamin A (as retinyl acetate)", "dose": "56,800 IU"},
    {"name": "Vitamin B6 (as P5P, pyridoxal 5-phosphate)", "dose": "2 mg"},
    {"name": "Vitamin B9 (as 5-MTHF, methyl folate)", "dose": "100 mcg"},
    {"name": "Vitamin B12 (as adenosyl and methyl cobalamin)", "dose": "6 mcg"},
    {"name": "Vitamin B15 (as pangamic acid)", "dose": "40 mg"},
    {"name": "Vitamin B15 (as trimethylglycine, from Beta spp.)", "dose": "10 mg"},
    {"name": "Vitamin B17 (amygdalin, from Prunus spp.)", "dose": "200 mg"},
    {"name": "Vitamin C (as magnesium ascorbate)", "dose": "60 mg"},
    {"name": "Vitamin D3 (as cholecalciferol)", "dose": "46 mcg (1,840 IU)"},
    {"name": "Vitamin E (as d-alpha tocopherol succinate)", "dose": "30 IU"},
    {"name": "Magnesium (as magnesium ascorbate and threonate)", "dose": "4 mg"},
    {"name": "Manganese (as manganese aspartate)", "dose": "0.56 mg"},
    {"name": "Selenium (as methylselenocysteine)", "dose": "70 mcg"},
    {"name": "Zinc (as zinc orotate)", "dose": "3 mg"},
    {"name": "Blushwood 100:1 (Fontainea spp.)", "dose": "50 mg"},
    {"name": "French lilac 20:1 (Galega officinalis)", "dose": "30 mg"},
    {"name": "Mistletoe 4:1 (Viscum album)", "dose": "10 mg"},
]
PINS = ["directions", "ingredients", "intro", "description", "benefits", "research"]
INTRO = ("Amygdalin (vitamin B17) from apricot kernels, with both forms of vitamin B15 and "
         "a group of vitamin, mineral and botanical synergists.")
DESCRIPTION = ("A Healing Sacrament, handcrafted with Aloha on the Healing Island of "
               "Hawai\u02bbi.\n\nOther ingredients: pullulan vegicaps, Healing Energy, Love "
               "and Prayer.\n\n30 vegicaps.\n\nCaution: " + WARNING)


def test_the_record_carries_the_approved_warning_and_pinned_dose():
    p = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                    "products.json")))["products"][SLUG]
    assert p["warning"] == WARNING and p["directions"] == DIRECTIONS
    assert p["copy_pinned"] == PINS and p["ingredients"] == PANEL
    # Run 188 is retinyl acetate, as the printed label says (formulation, 2026-10-25).
    assert p["ingredients"][0] == {"name": "Vitamin A (as retinyl acetate)", "dose": "56,800 IU"}
    # The approved store page (b17-syntropy-store-page.md): 17 lines, no blend.
    assert len(p["ingredients"]) == 17
    assert not any("Bioavailability" in i["name"] for i in p["ingredients"])
    assert p["description"] == DESCRIPTION and p["intro"] == INTRO
    assert p["benefits"] == [] and p["how_it_works"] == ""


@pytest.mark.parametrize("q", ["How much B17 Syntropy should I take?",
                               "i feel lightheaded on b17 syntropy",
                               "Can I take 10 B17 Syntropy a day?"])
def test_the_chat_gets_one_dose_and_the_whole_warning(q):
    b = app._named_product_facts(q)
    assert "### B17 Syntropy" in b
    assert "Directions: " + DIRECTIONS in b
    assert WARNING in b


def test_the_page_shows_the_dose_and_the_caution(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    d = app.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = {s["id"]: s for s in d["sections"]}
    assert sec["caution"]["body"] == WARNING
    assert sec["ingredients"]["body"]["directions"] == DIRECTIONS


def test_content_since_is_a_full_timestamp():
    # Caches store UTC times, and "2026-10-25T21:50Z" sorts after a bare "2026-10-25",
    # so a date alone kept that day's drafts (review round 1, 2026-10-25).
    p = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                    "products.json")))["products"][SLUG]
    assert len(p["content_since"]) > 10 and p["content_since"] > "2026-10-25T21:50:00"


def test_the_corrections_replay_keeps_the_approved_record():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    data = os.path.join(os.path.dirname(__file__), "..", "data")
    products = json.load(open(os.path.join(data, "products.json")))["products"]
    corr = json.load(open(os.path.join(data, "products-manual-corrections.json")))
    rec = dict(products[SLUG])
    # What an enrichment run writes back first: the old store panel, no warning or dose.
    rec.update(ingredients=[{"name": "Vitamin A (Retinyl Palmitate)", "dose": "5000 IU"}],
               description="Titrate upward to a maximum of ten capsules daily.",
               intro="Generated intro.", ingredients_source="t33")
    for k in ("warning", "directions", "copy_pinned", "content_since"):
        rec.pop(k, None)
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    assert p[SLUG]["ingredients"] == PANEL and p[SLUG]["copy_pinned"] == PINS
    assert p[SLUG]["warning"] == WARNING and p[SLUG]["directions"] == DIRECTIONS
    assert p[SLUG]["content_since"] == products[SLUG]["content_since"]
    assert p[SLUG]["ingredients_source"] == "label-2026-06-run-188"
    assert p[SLUG]["description"] == DESCRIPTION and p[SLUG]["intro"] == INTRO


def test_the_chat_gets_the_only_dose_rule():
    b = app._named_product_facts("Can I take 10 B17 Syntropy a day?")
    assert "The Directions above are the only dose for B17 Syntropy." in b
    assert "such as up to 10 capsules a day" in b


def test_a_symptom_is_answered_with_stop_and_call():
    b = app._named_product_facts("I feel lightheaded on B17 Syntropy, should I take less?")
    assert app._STOP_AT_SYMPTOM_RULE in b
    # A caution without Poison Control gets no such rule.
    assert app._STOP_AT_SYMPTOM_RULE not in app._named_product_facts(
        "How much Vitamin D Syntropy should I take?")


def test_the_learn_page_is_the_approved_description(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    md = app.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()["markdown"]
    assert md == DESCRIPTION
    # No diet claim while its D3 source is unknown (Glen, 2026-10-25).
    for old in ("5000 IU", "Retinyl Palmitate", "ten capsules", "titrat", "vegetarian", "vegan"):
        assert old.lower() not in md.lower()


def test_the_page_has_no_generated_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_SALES_AI_COPY_ENABLED", True)
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    d = app.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = {s["id"]: s for s in d["sections"]}
    for key in ("intro", "description", "research"):
        assert "ai" not in sec[key], key
    assert sec["description"]["body"] == DESCRIPTION and sec["intro"]["body"] == INTRO
