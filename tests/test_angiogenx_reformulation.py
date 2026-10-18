"""AngiogenX: the October 2026 five-ingredient eye formula, one capsule a day.

Spec: production/05 Formulations/_store-updates/2026-10-06-angiogenx.md (Glen "send it",
2026-10-07, via formulation). FileMaker 411 holds it. The copy is pinned and the three approved
photos go in the `images` gallery.

How it works, learn text, benefits and chat roles: 2026-10-11-angiogenx-how-it-works.md (Glen
"yes", 2026-10-11, via formulation), pinned verbatim.
"""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "angiogenx"
FIVE = [
    ("Pine Bark OPCs 95% (Pinus pinaster) (cortex)", "158 mg"),
    ("Trans-Resveratrol 98% (Polygonum cuspidatum) (radix)", "100 mg"),
    ("Curcumin 95% (Curcuma longa) (rhizoma)", "100 mg"),
    ("Notoginsenosides 80% (Panax notoginseng) (radix)", "75 mg"),
    ("Crocin 45% (Gardenia jasminoides) (fructus)", "33 mg"),
]
INTRO = ("AngiogenX supports the health of the small blood vessels in your eyes. It combines pine "
         "bark OPCs, trans-resveratrol, curcumin, notoginseng and crocin from gardenia fruit.")
WARNING = ("Avoid use while healing a wound or any other tissue injury, such as a cut, fracture, "
           "sprain or burn. Also avoid use during menses or in pregnancy. Stop a few days before "
           "surgery. Resume once healing is complete. If you take a blood thinner, consult your "
           "physician before use.")
DESCRIPTION = "\n\n".join([
    "Each vegicap supplies 158 mg pine bark OPCs 95%, 100 mg trans-resveratrol 98%, 100 mg "
    "curcumin 95%, 75 mg notoginsenosides 80% and 33 mg crocin 45%. There are no fillers or "
    "excipients.",
    "Other ingredients: Certified Organic Pullulan Vegicap, Healing Energy, Love, and Prayer. "
    "30 vegicaps per bottle.",
    "Caution: " + WARNING,
])
DIRECTIONS = "Take 1 capsule daily with food, or as guided."
PHOTOS = ["/static/product-photos/angiogenx-1.webp",
          "/static/product-photos/angiogenx-2.webp",
          "/static/product-photos/angiogenx-3.webp"]
HOW = 'The retina depends on a dense network of very small blood vessels. Here is what each ingredient in AngiogenX does.\n\nIn human eye studies, a pine bark extract helped small blood vessels keep fluid inside. One study used 150 mg of that extract a day.\n\nIn animal studies, resveratrol helped regulate the signals that drive new blood vessel growth. After oral doses, it and its breakdown products have been found in human eye tissue.\n\nIn an animal study, curcumin helped calm inflammatory signals in the retina.\n\nIn an animal study, notoginseng saponins helped keep the barrier between blood and retina intact.\n\nCrocin is a pigment found in gardenia fruit. It supports the retina. Two human eye trials used 15 mg a day, the amount in one capsule.'
LEARN = 'The retina depends on a dense network of very small blood vessels. Here is what each ingredient in AngiogenX does.\n\nIn human eye studies, a pine bark extract helped small blood vessels keep fluid inside. One study used 150 mg of that extract a day.\n\nIn animal studies, resveratrol helped regulate the signals that drive new blood vessel growth. After oral doses, it and its breakdown products have been found in human eye tissue.\n\nIn an animal study, curcumin helped calm inflammatory signals in the retina.\n\nIn an animal study, notoginseng saponins helped keep the barrier between blood and retina intact.\n\nCrocin is a pigment found in gardenia fruit. It supports the retina. Two human eye trials used 15 mg a day, the amount in one capsule.\n\n## Sources\n\n### Pine bark OPCs\n- [Treatment of vascular retinopathies with Pycnogenol](https://pubmed.ncbi.nlm.nih.gov/11351356/). Phytother Res, 2001.\n- [Pycnogenol improves microcirculation, retinal edema, and visual acuity in early diabetic retinopathy](https://pubmed.ncbi.nlm.nih.gov/19916788/). J Ocul Pharmacol Ther, 2009.\n\n### Resveratrol\n- [Resveratrol Inhibits Hypoxia-Induced Vascular Endothelial Growth Factor Expression and Pathological Neovascularization](https://pubmed.ncbi.nlm.nih.gov/26446654/). Yonsei Med J, 2015.\n- [Resveratrol inhibits pathologic retinal neovascularization in Vldlr(-/-) mice](https://pubmed.ncbi.nlm.nih.gov/21282584/). Invest Ophthalmol Vis Sci, 2011.\n- [Tissue Distribution of trans-Resveratrol and Its Metabolites after Oral Administration in Human Eyes](https://pubmed.ncbi.nlm.nih.gov/28409021/). J Ophthalmol, 2017.\n\n### Curcumin\n- [Curcumin prevents experimental diabetic retinopathy in rats through its hypoglycemic, antioxidant, and anti-inflammatory mechanisms](https://pubmed.ncbi.nlm.nih.gov/21314438/). J Ocul Pharmacol Ther, 2011.\n\n### Notoginseng\n- [Panax notoginseng saponins alleviate diabetic retinopathy by inhibiting retinal inflammation: Association with the NF-κB signaling pathway](https://pubmed.ncbi.nlm.nih.gov/37689326/). J Ethnopharmacol, 2024.\n\n### Crocin\n- [Effects of Crocin on Diabetic Maculopathy: A Placebo-Controlled Randomized Clinical Trial](https://pubmed.ncbi.nlm.nih.gov/29550187/). Am J Ophthalmol, 2018.\n- [Evaluation of the monotherapy effect of crocin on mild-to-moderate diabetic retinopathy: A randomized clinical trial](https://pubmed.ncbi.nlm.nih.gov/42011352/). Avicenna J Phytomed, 2026.'
BENEFITS = ["Helps small blood vessels keep fluid where it belongs", "Supports the retina with ingredients used in human eye trials"]
ROLES = {
    "Pine Bark OPCs 95% (Pinus pinaster) (cortex)": "Helped small blood vessels keep fluid inside in human eye studies of a pine bark extract.",
    "Trans-Resveratrol 98% (Polygonum cuspidatum) (radix)": "Helped regulate the signals that drive new blood vessel growth in animal studies.",
    "Curcumin 95% (Curcuma longa) (rhizoma)": "Helped calm inflammatory signals in the retina in an animal study.",
    "Notoginsenosides 80% (Panax notoginseng) (radix)": "Helped keep the barrier between blood and retina intact in an animal study.",
    "Crocin 45% (Gardenia jasminoides) (fructus)": "Supports the retina and has been used in human eye trials."
}
PINS = ["ingredients", "intro", "description", "benefits", "research", "roles"]
# The 9 approved source lines on the learn page. Glen, 2026-10-11: list the sources with
# their PubMed titles, which may name a condition. Only these exact lines are exempt from
# the old-word scan; any other line under a source heading is still scanned.
SOURCE_LINES = [
    "- [Treatment of vascular retinopathies with Pycnogenol](https://pubmed.ncbi.nlm.nih.gov/11351356/). Phytother Res, 2001.",
    "- [Pycnogenol improves microcirculation, retinal edema, and visual acuity in early diabetic retinopathy](https://pubmed.ncbi.nlm.nih.gov/19916788/). J Ocul Pharmacol Ther, 2009.",
    "- [Resveratrol Inhibits Hypoxia-Induced Vascular Endothelial Growth Factor Expression and Pathological Neovascularization](https://pubmed.ncbi.nlm.nih.gov/26446654/). Yonsei Med J, 2015.",
    "- [Resveratrol inhibits pathologic retinal neovascularization in Vldlr(-/-) mice](https://pubmed.ncbi.nlm.nih.gov/21282584/). Invest Ophthalmol Vis Sci, 2011.",
    "- [Tissue Distribution of trans-Resveratrol and Its Metabolites after Oral Administration in Human Eyes](https://pubmed.ncbi.nlm.nih.gov/28409021/). J Ophthalmol, 2017.",
    "- [Curcumin prevents experimental diabetic retinopathy in rats through its hypoglycemic, antioxidant, and anti-inflammatory mechanisms](https://pubmed.ncbi.nlm.nih.gov/21314438/). J Ocul Pharmacol Ther, 2011.",
    "- [Panax notoginseng saponins alleviate diabetic retinopathy by inhibiting retinal inflammation: Association with the NF-κB signaling pathway](https://pubmed.ncbi.nlm.nih.gov/37689326/). J Ethnopharmacol, 2024.",
    "- [Effects of Crocin on Diabetic Maculopathy: A Placebo-Controlled Randomized Clinical Trial](https://pubmed.ncbi.nlm.nih.gov/29550187/). Am J Ophthalmol, 2018.",
    "- [Evaluation of the monotherapy effect of crocin on mild-to-moderate diabetic retinopathy: A randomized clinical trial](https://pubmed.ncbi.nlm.nih.gov/42011352/). Avicenna J Phytomed, 2026."
]
OLD = ("Mistletoe", "Honokiol", "Wormwood", "Green Tea", "Saffron", "Vitamin D3", "Sumac",
       "EMIQ", "macular degeneration", "retinopathy", "malignanc", "glaucoma",
       # Condition words the approved source titles carry; our own copy names none.
       "diabetic", "edema", "maculopathy", "neovascularization")


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_five_ingredients_in_order_and_the_copy(fname):
    e = _entry(fname)
    assert [(i["name"], i["dose"]) for i in e["ingredients"]] == FIVE
    assert e["ingredients_source"] == "label-2026-10-05"
    assert e["description"] == DESCRIPTION
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["bottle_type"] == "30 Caps"


def test_the_store_entry():
    e = _entry()
    assert e["intro"] == INTRO
    assert e["copy_pinned"] == PINS
    assert e["content_since"] == "2026-10-08"
    assert e["price_cents"] == 7000 and e["fmp_id"] == "411" and e["name"] == "AngiogenX"
    assert e["benefits"] == BENEFITS and e["how_it_works"] == HOW
    assert e["learn_markdown"] == LEARN and e["ingredient_roles"] == ROLES
    # Marketing retired the GrooveKart page (404, 2026-10-07), so the flag is cleared.
    for gone in ("gk_stale", "gk_stale_reason", "gk_extra_accepted"):
        assert gone not in e, gone
    scanned = dict(e)
    scanned["learn_markdown"] = "\n".join(
        ln for ln in e["learn_markdown"].split("\n") if ln not in SOURCE_LINES)
    assert scanned["learn_markdown"] != e["learn_markdown"]
    text = json.dumps(scanned)
    for gone in OLD:
        assert gone.lower() not in text.lower(), gone


def test_an_apply_run_cannot_set_the_stale_flag_back():
    # scripts/apply_enrichment.py sets gk_stale only when the clean entry carries it.
    clean = json.load(open(os.path.join(ROOT, "data", "products-enrich-clean.json"),
                           encoding="utf-8"))[SLUG]
    assert "gk_stale" not in clean and "stale_reason" not in clean


def test_the_three_approved_photos_in_order():
    images = _entry()["images"]
    assert [i["src"] for i in images] == PHOTOS
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path
        assert i["alt"].strip(), path


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def test_no_cached_ai_draft_replaces_the_pinned_copy(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.init_table(cx)
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER mistletoe wet AMD")
        # Dated after content_since, so the reset does not remove it: only the pin can.
        cx.execute("UPDATE sales_pages SET generated_at='2099-01-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    dump = json.dumps(d)
    assert "AI DRAFT MARKER" not in dump and "AI HOW MARKER" not in dump
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    for sec in ("intro", "description", "research"):
        assert "ai" not in secs[sec], sec
    assert secs["research"]["body"]["how_it_works"] == HOW
    body = secs["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == FIVE
    assert body["directions"] == DIRECTIONS and body["warning"] == WARNING
    assert [i["src"] for i in d["images"]] == PHOTOS


def test_product_data_sends_the_approved_benefits_and_how_it_works(appmod):
    d = appmod.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["benefits"] == BENEFITS
    assert d["how_it_works"] == HOW
    assert d["description"] == DESCRIPTION
    assert [i["src"] for i in d["images"]] == PHOTOS


def test_the_learn_page_serves_the_text_and_sources(appmod):
    d = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == LEARN
    assert sum(1 for ln in d["markdown"].split("\n") if ln in SOURCE_LINES) == 9


def test_every_role_names_a_label_line_and_no_dose():
    e = _entry()
    names = [i["name"] for i in e["ingredients"]]
    assert sorted(e["ingredient_roles"]) == sorted(names)
    for role in e["ingredient_roles"].values():
        assert "mg" not in role and not any(ch.isdigit() for ch in role), role


def test_the_corrections_file_carries_the_new_fields():
    e, c = _entry(), _entry("products-manual-corrections.json")
    for field in ("intro", "how_it_works", "learn_markdown", "benefits", "ingredient_roles",
                  "copy_pinned"):
        assert c[field] == e[field], field


def test_the_enrichment_replay_restores_the_new_fields():
    import copy
    from scripts.apply_enrichment import apply_corrections
    products = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    corrections = json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))
    fields = ("intro", "how_it_works", "learn_markdown", "benefits", "ingredient_roles",
              "copy_pinned")
    for stale in (False, True):
        damaged = copy.deepcopy(products)
        for field in fields:
            if stale:   # an unapproved value is overwritten, not only a missing one filled
                damaged[SLUG][field] = (["junk"] if field in ("benefits", "copy_pinned")
                                        else {"junk": "x"} if field == "ingredient_roles"
                                        else "junk")
            else:
                damaged[SLUG].pop(field, None)
        for _ in range(2):   # idempotent
            apply_corrections(damaged, copy.deepcopy(corrections))
            for field in fields:
                assert damaged[SLUG].get(field) == products[SLUG][field], (stale, field)


def test_the_chat_carries_each_role_beside_its_own_ingredient(appmod):
    block = appmod._named_product_facts("What does each ingredient in AngiogenX do?")
    for name, dose in FIVE:
        # The whole line: label name, panel dose, approved role. Nothing else on it.
        assert block.count(f"\n- {name}: {dose}. Role: {ROLES[name]}\n") == 1, name
    assert block.count("Role: ") == 5
    assert ("Ingredient roles for AngiogenX: when you say what an ingredient in AngiogenX does, "
            "give only the Role written beside it, in its words. An ingredient with no Role gets "
            "no role: list it without one. Add no organ, pathway, phase or cofactor that the Role "
            "does not state.") in block
    other = appmod._named_product_facts("What is Angiostasis?")
    assert "Role:" not in other
