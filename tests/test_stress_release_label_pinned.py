"""Stress Release's page carries the December 2025 label, pinned (Glen "yes", 2026-10-23).

What this does: production/05 Formulations/stress-release/2026-10-23/stress-release-store-page.md
lists the label's lines. Glen confirmed the three amounts where FileMaker 442 differs:
CBD 10 mg, macamides 40 mg and salidrosides 6 mg. FileMaker said 8, 5 and 5 mg, and the
page showed those. The unreviewed AI text said "8 mg of CBD" and "take it whenever", and
the learn page named reishi, cordyceps, citrus pectin and a bioavailability blend, none of
which is in the formula. The label dose is 1 capsule at bedtime or up to 3 a day.

No research text is approved yet. The research section is pinned empty, so no AI draft
fills it and it shows only the learn page link. The learn page falls back to the approved
overview. The draft's slow-release sentences stay out of it: under "The research" they read
as studies (review round 2).

The approved text is written out here, because CI has no vault to read it from.
"""
import copy
import importlib
import importlib.util
import json
import os
import sqlite3

import pytest

SLUG = "stress-release"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def entry():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"][SLUG]


INTRO = [
    "Stress Release pairs CBD with thirteen natural concentrates chosen to support calm and resilience.",
    "A gentle plant gel releases them slowly through the day.",
]
GEL = [
    "The slow release comes from konjac root and xanthan gum.",
    "The formula contains no phthalates, the plasticizers used in many time-release products.",
]
DIRECTIONS = "Take 1 capsule at bedtime, or up to 3 times a day as needed with food, or as guided."
OVERVIEW = INTRO + GEL + [DIRECTIONS]
OTHER = "Other ingredients: pullulan vegicap, Healing Energy, Love, and Prayer."
INGREDIENTS = [
    ("GABA (gamma-aminobutyric acid)", "30 mg"),
    ("Green tea L-theanine 99% (Camellia sinensis)", "30 mg"),
    ("Saffron safranal 3% (Crocus sativus)", "5 mg"),
    ("Maca macamides 60% (Lepidium meyenii)", "40 mg"),
    ("Phosphatidylserine 50%, from soy (Glycine max)", "65 mg"),
    ("Rhodiola salidrosides (Rhodiola rosea)", "6 mg"),
    ("Siberian ginseng eleutherosides B and E 2% (Eleutherococcus senticosus)", "80 mg"),
    ("Celery seed apigenin 98% (Apium graveolens)", "15 mg"),
    ("Magnolia honokiol 50% (Magnolia officinalis)", "7 mg"),
    ("Passionflower flavones 10% (Passiflora incarnata)", "80 mg"),
    ("Rosemary rosmarinic acid 50% (Salvia rosmarinus)", "10 mg"),
    ("Ashwagandha withanolides 20% (Withania somnifera)", "25 mg"),
    ("CBD (cannabidiol, Cannabis spp.)", "10 mg"),
    ("Holy basil ursolic acid 50% (Ocimum sanctum)", "100 mg"),
    ("Slow release gel: konjac glucomannan (Amorphophallus konjac) and xanthan gum "
     "(Xanthomonas campestris)", "25 mg"),
]
# Each row links to the ingredient page that already exists for it, as Clarity's do.
PAGES = ["gaba", "l-theanine", "saffron", "maca-60-macamides-organic-extract",
         "phosphatidyl-serine", "salidrosides-98-rhodiola-rosea", "siberian-ginseng",
         "apigenin", "honokiol", "wild-passion-flower-maypop-apricot-vine-", "rosmarinic-acid",
         "ashwagandha-root-extract-ayurvedic-ginse", "cbd", "holy-basil",
         "glucomannan-amorphophallus-konjac"]
# Words from the old copy that describe what is not in the formula, or the wrong dose.
STALE = ["8 mg", "whenever", "whatever time", "any time of day", "Reishi", "Cordyceps",
         "Citrus Pectin", "Mastic", "Bioavailability", "Ginkgo", "AI DRAFT MARKER"]


def _rows(ings):
    return [(i["name"], i["dose"]) for i in ings]


def test_the_panel_is_the_label(entry):
    assert _rows(entry["ingredients"]) == INGREDIENTS
    doses = dict(_rows(entry["ingredients"]))
    assert doses["CBD (cannabidiol, Cannabis spp.)"] == "10 mg"
    assert doses["Maca macamides 60% (Lepidium meyenii)"] == "40 mg"
    assert doses["Rhodiola salidrosides (Rhodiola rosea)"] == "6 mg"
    assert entry["panel_note"] == OTHER


def test_the_prose_is_the_approved_draft(entry):
    assert entry["intro"] == "\n\n".join(INTRO)
    assert entry["description"] == "\n\n".join(OVERVIEW)
    assert not entry.get("how_it_works")
    assert entry["directions"] == DIRECTIONS
    assert "learn_markdown" not in entry and "research_links" not in entry


def test_the_four_sections_are_pinned(entry):
    assert sorted(entry["copy_pinned"]) == ["description", "ingredients", "intro", "research"]
    assert entry["content_since"] == "2026-10-24T07:27:00"


def test_each_row_links_to_the_existing_page_for_its_ingredient(entry):
    from dashboard import ingredients as ing
    assert [i["page_slug"] for i in entry["ingredients"]] == PAGES
    for i in entry["ingredients"]:
        assert ing.resolve(i["page_slug"])["name"] == i["page_name"], i["page_slug"]


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD 8 mg of CBD, take it whenever",
                                   "ingredients": [{"name": "CBD", "dose": "8 mg"}],
                                   "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW Reishi and Cordyceps")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def _page(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, f"AI DRAFT MARKER {sec} 8 mg of CBD, whenever")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    return d, {s["id"]: s for s in d["sections"]}


def test_the_page_shows_the_label_over_cached_ai_drafts(appmod):
    d, secs = _page(appmod)
    assert secs["intro"]["body"] == "\n\n".join(INTRO)
    assert secs["description"]["body"] == "\n\n".join(OVERVIEW)
    assert secs["research"]["body"] == {"how_it_works": "",
                                        "learn_url": "/begin/learn/stress-release"}
    for sec in ("intro", "description", "research"):
        assert "ai" not in secs[sec], sec
    ing = secs["ingredients"]["body"]
    assert _rows(ing["ingredients"]) == INGREDIENTS
    assert [i["slug"] for i in ing["ingredients"]] == PAGES
    assert ing["directions"] == DIRECTIONS
    assert ing["note"] == OTHER
    whole = json.dumps(d)
    for s in STALE:
        assert s.lower() not in whole.lower(), s


def test_the_learn_page_shows_the_approved_overview(appmod):
    d = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == "\n\n".join(OVERVIEW)
    for s in STALE:
        assert s.lower() not in json.dumps(d).lower(), s


def test_the_bot_gets_the_label_amounts_and_dose(appmod):
    block = appmod._named_product_facts("How much Stress Release should I take?")
    assert "### Stress Release" in block
    assert DIRECTIONS in block
    assert "- CBD (cannabidiol, Cannabis spp.): 10 mg" in block
    assert "- Maca macamides 60% (Lepidium meyenii): 40 mg" in block
    assert "- Rhodiola salidrosides (Rhodiola rosea): 6 mg" in block
    assert "8 mg" not in block


def test_replaying_the_manual_corrections_keeps_the_label(entry):
    spec = importlib.util.spec_from_file_location(
        "apply_enrichment", os.path.join(ROOT, "scripts", "apply_enrichment.py"))
    ae = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ae)
    corr = json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))
    fields = ("intro", "description", "ingredients", "panel_note",
              "directions", "copy_pinned", "content_since")
    # A rebuilt record: FileMaker's amounts and the old scraped copy back.
    bare = {k: v for k, v in copy.deepcopy(entry).items() if k not in fields}
    bare.update(description="Stress Release can be taken at whatever time of day", intro="",
                ingredients=[{"name": "CBD", "dose": "8 mg"}])
    products = {SLUG: bare}
    assert ae.apply_corrections(products, {SLUG: corr[SLUG]}) == [SLUG]
    for f in fields:
        assert products[SLUG][f] == entry[f], f
    assert products[SLUG]["ingredients_source"] == entry["ingredients_source"]


@pytest.mark.parametrize("q", [
    "Is the MB5 ESR Emotional Stress Release Infoceutical good for me?",
    "How do I use MB5 ESR Emotional Stress Release Infoceutical?",
])
def test_the_bot_does_not_bring_stress_release_for_the_infoceutical(appmod, q):
    assert "### Stress Release" not in appmod._named_product_facts(q)


def test_a_standalone_stress_release_after_the_infoceutical_still_counts(appmod):
    q = "Is MB5 ESR Emotional Stress Release Infoceutical like Stress Release?"
    assert "### Stress Release" in appmod._named_product_facts(q)
