"""Vitreous Vitality's app page and chat carry the store page's caution, dose and panel.

FileMaker 594, run 99. Each capsule supplies about 8,520 mcg RAE (28,400 IU) of vitamin A,
nearly 3 times the adult upper limit, and the app page had no caution and no directions
while the ebook linked buyers to it. Glen said "yes" in the platform tab on 2026-10-26 to
the text he approved for remedymatch.com 315 (formulation spec
production/05 Formulations/vitreous-vitality/2026-10-26/app-store-spec.md). The spec also
takes lutein, which is not in the formula, out of the intro and floaters out of the overview.
"""
import json
import os

import pytest

import app

SLUG = "vitreous-vitality"
DIRECTIONS = "1 capsule daily with food, as guided."
VITAMIN_A = {"name": "Vitamin A (retinyl acetate)",
             "dose": "8,520 mcg RAE (28,400 IU), 947% of the Daily Value"}
CAUTION_START = "Each capsule supplies 8,520 mcg RAE (28,400 IU) of preformed vitamin A."
PREGNANCY = "Do not use if you are pregnant, may become pregnant or are breastfeeding."
CAUTION_END = "Consult first too if you have a bleeding disorder, or before planned surgery. Keep out of reach of children."
# Word for word from remedymatch.com 315 (marketing apply-long.html, 2026-10-26).
CAUTION = (
    "Each capsule supplies 8,520 mcg RAE (28,400 IU) of preformed vitamin A. "
    "That is nearly 3 times the adult daily upper limit of 3,000 mcg RAE (10,000 IU). "
    "The limit counts preformed vitamin A from food and supplements together. "
    "The jar label says retinyl palmitate 2,500 IU and up to 3 capsules a day. "
    "That label is wrong for this batch. "
    "Follow this page instead. "
    "Take no more than 1 capsule a day, and only as guided by your health professional. "
    "Do not take it for longer than 6 months unless your health professional advises it. "
    "Do not add other sources of preformed vitamin A. "
    "These include multivitamins, cod liver oil, liver and other formulas that list vitamin A, including our own. "
    "Do not use with retinoid medicines such as isotretinoin or acitretin. "
    "Do not use with tetracycline antibiotics such as doxycycline or minocycline. "
    "Do not use if you are pregnant, may become pregnant or are breastfeeding. "
    "Do not give it to anyone under 18. "
    "Stop and seek advice if you notice headache, blurred vision, nausea, dizziness, bone or joint pain, or dry, peeling skin. "
    "Do not use if you have Stargardt disease or another ABCA4 retinal condition. "
    "Do not use if you are allergic to pineapple. "
    "Consult your health professional before use if you have liver or kidney disease or drink alcohol daily. "
    "Consult your health professional before long-term use if you have osteoporosis or low bone density. "
    "Also consult first if you take any medicine, especially blood-thinning or blood-sugar medicines. "
    "This includes aspirin and ibuprofen. "
    "Consult first too if you have a bleeding disorder, or before planned surgery. "
    "Keep out of reach of children.")
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


def test_the_record_carries_the_store_text():
    p = _record()
    assert p["directions"] == DIRECTIONS
    assert p["ingredients"][0] == VITAMIN_A
    from dashboard.products import shown_ingredients
    assert len(shown_ingredients(p["ingredients"])) == 34
    # The 0 mg rows stay as Glen's formulation notes, hidden on the page.
    assert {"name": "Goji", "dose": "0 mg"} in p["ingredients"]
    assert p["chat_caution"] is True
    assert p["warning"] == CAUTION
    assert json.load(open(os.path.join(DATA, "products-manual-corrections.json")))[SLUG][
        "warning"] == CAUTION
    assert "28,500" not in json.dumps(p)
    for k in ("intro", "description"):
        assert "lutein" not in p[k].lower() and "floater" not in p[k].lower(), k
    names = " ".join(i["name"] for i in p["ingredients"])
    assert "Retinyl Acetate" not in names and "Magnesium L-Threonate" not in names


def test_the_page_data_shows_the_caution_and_directions():
    r = app.app.test_client().get(f"/begin/product-page-data/{SLUG}")
    assert r.status_code == 200
    d = r.get_json()
    s = json.dumps(d)
    assert CAUTION_START in s and PREGNANCY in s and CAUTION_END in s
    assert f'"directions": "{DIRECTIONS}"' in s
    assert '"dose": "8,520 mcg RAE (28,400 IU), 947% of the Daily Value"' in s
    sec = {x["id"]: x for x in d["sections"]}
    for k in ("intro", "description"):
        assert "lutein" not in json.dumps(sec[k]["body"]).lower(), k
        assert "floater" not in json.dumps(sec[k]["body"]).lower(), k
    assert '"10 mg"' not in json.dumps(sec["ingredients"]["body"]["ingredients"][0])


@pytest.mark.parametrize("q", [
    "How many Vitreous Vitality capsules a day?",
    "How much vitamin A is in Vitreous Vitality?",
    "vitreous vitality dose?",
    "how many vitreous vitality capsules can I take?",
])
def test_the_chat_gets_the_dose_and_the_caution(q):
    b = app._named_product_facts(q)
    assert "### Vitreous Vitality\n" in b
    assert f"Directions: {DIRECTIONS}" in b
    assert "- Vitamin A (retinyl acetate): 8,520 mcg RAE (28,400 IU), 947% of the Daily Value" in b
    assert "The Directions above are the only dose for Vitreous Vitality." in b
    assert "such as up to 3 capsules a day" in b
    assert "Give no percent Daily Value that is not listed above" in b
    assert f"Caution: {CAUTION}" in b
    assert "has no caution on its product page" not in b
    assert "### Vitality\n" not in b


@pytest.mark.parametrize("q", [
    "Can I take Vitreous Vitality while pregnant?",
    "Is Vitreous Vitality safe with warfarin?",
    "is vitreous vitality safe?",
])
def test_a_safety_question_links_its_own_caution(q):
    """Glen, 2026-10-24: a question that touches a caution gets it linked, not written."""
    links = app._caution_link_slugs(q)
    assert SLUG in links and "vitality" not in links
    b = app.named_product_facts_block(q)
    assert "### Vitreous Vitality\n" in b and "### Vitality\n" not in b


@pytest.mark.parametrize("q", [
    "Is Vitreous-Vitality safe?",
    "Vitreus Vitality dose?",
    "how much vitreus vitality a day?",
    "Vitrious Vitality dose?",
    "Vitrous Vitality dose?",
    "Vitreous/Vitality dose?",
])
def test_a_misspelt_name_never_brings_vitality(q):
    """Review rounds 1 and 3: these brought Vitality's block and its caution."""
    assert "### Vitality\n" not in app._named_product_facts(q)
    assert "vitality" not in app._caution_link_slugs(q)


@pytest.mark.parametrize("q", [
    "Is Vitreous-Vitality safe?",
    "vitreous-vitality dose?",
    "Vitreous - Vitality dose?",
    "Vitreous/Vitality dose?",
])
def test_a_joined_name_brings_its_own_block(q):
    """Review round 3: a hyphen between the words brought no block at all."""
    assert "### Vitreous Vitality\n" in app._named_product_facts(q)


@pytest.mark.parametrize("q", [
    "Is Vitreous-Vitality safe with Vitality?",
    "Vitreus Vitality or Vitality, which is safer?",
])
def test_a_later_vitality_still_counts(q):
    """Review round 3: the first, misspelt occurrence hid a later standalone Vitality."""
    assert "### Vitality\n" in app._named_product_facts(q)


@pytest.mark.parametrize("q", [
    "How do I keep my vitreous vitality as I age?",
    "what supports vitreous vitality naturally",
])
def test_the_lowercase_phrase_brings_no_block(q):
    assert "### Vitreous Vitality\n" not in app._named_product_facts(q) + "\n"


def test_vitality_itself_keeps_its_block():
    assert "### Vitality\n" in app._named_product_facts("Is Vitality safe with my blood thinner?")


def test_the_corrections_replay_restores_the_store_text():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    corr = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))
    approved = _record()
    # What a re-import writes back: the formulation database's raw weights, no text.
    rec = dict(approved, ingredients=[{"name": "Retinyl Acetate", "dose": "10 mg"}],
               directions="", warning="", chat_caution=False, copy_pinned=[],
               intro="Retinyl acetate, zinc carnosine, lutein, and crocin.",
               description="Whether you're concerned about floaters.")
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    for k in ("ingredients", "directions", "warning", "chat_caution", "copy_pinned",
              "intro", "description"):
        assert p[SLUG][k] == approved[k], k


def test_a_safety_question_never_gets_a_yes():
    """Live, log 2416: "Is Vitreous Vitality safe?" got "Yes, Vitreous Vitality is
    formulated to support eye health"."""
    b = app.named_product_facts_block("Is Vitreous Vitality safe?")
    assert ("Never say Vitreous Vitality is safe, and never answer yes or no to whether it "
            "is safe.") in b
