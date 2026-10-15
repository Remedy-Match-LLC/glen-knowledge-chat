"""The Sleep Bundle's approved intro and description (formulation spec, sha 13e6a646:
production/05 Formulations/_store-updates/2026-10-15-sleep-bundle-copy.md, Glen "approve"
and "send it", 2026-10-15).

The intro carries the directions; the cautions stay in their own sections and reach the
bot from the components. A live answer had given old dose advice from the knowledge base,
so the bot now gets the approved directions and the dose rule.
"""
import copy
import importlib
import json
import re

import pytest

import app

INTRO = "\n\n".join([
    "The Sleep Bundle includes Brain Cleanse, Sleep Syntropy, the Therapeutic Nightlight and "
    "the Biocompatible Nightlight.",
    "Take 1 capsule of Brain Cleanse at bedtime and turn the lights out within 30 minutes. "
    "Take 1 capsule of Sleep Syntropy in the middle of the night to help you fall back to "
    "sleep. You may also take it at bedtime or at both times. If you need light during the "
    "night, use the red nightlights rather than white light.",
])
DESCRIPTION = "\n\n".join([
    "Brain Cleanse is the bedtime formula. It pairs 20 mg of melatonin with calming botanicals "
    "and amino acids to support the cleansing of the brain and nervous system while you sleep.",
    "Sleep Syntropy is the gentle formula for the middle of the night, with 250 mcg of "
    "melatonin. It helps you relax and fall back to sleep.",
    "White light at night suppresses the pineal gland's melatonin production. The two "
    "nightlights let you see in the dark using red light instead. The Therapeutic Nightlight "
    "uses 660 nm soft-laser red light and turns itself on in low light. The Biocompatible "
    "Nightlight filters white LED light through red for hallways, bathrooms and bedrooms.",
])
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))
SLUG = "sleep-bundle"
DOSE_RULE = "Give its dose from this text"


def _warning(slug):
    return PRODUCTS[slug]["warning"]


# ---- the stored text --------------------------------------------------------------

def test_both_files_hold_the_approved_text_pinned():
    for record in (PRODUCTS[SLUG], CORRECTIONS[SLUG]):
        assert record["intro"] == INTRO
        assert record["description"] == DESCRIPTION
        assert record["copy_pinned"] == ["intro", "description"]
        assert record["caution_components"] == ["brain-cleanse", "sleep-syntropy"]
        assert record["ingredients_source"] == "none"


def test_a_rebuild_restores_the_text_and_the_pin():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    for field in ("intro", "description", "copy_pinned"):
        products[SLUG].pop(field)
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[SLUG]["intro"] == INTRO
    assert products[SLUG]["description"] == DESCRIPTION
    assert products[SLUG]["copy_pinned"] == ["intro", "description"]
    assert products[SLUG]["ingredients_source"] == "none"


# ---- the page ---------------------------------------------------------------------

@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")
    return a


def test_the_page_shows_the_approved_text_and_both_caution_sections(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    assert secs["description"]["body"] == DESCRIPTION
    assert secs["description"]["keep_breaks"] is True
    assert "caution-brain-cleanse" in secs and "caution-sleep-syntropy" in secs


# ---- the chatbot ------------------------------------------------------------------

def _block(q, gated=False):
    return app.named_product_facts_block(q, gated=gated)


@pytest.mark.parametrize("q", ["Is the Sleep Bundle safe?", "is sleep bundle safe with my meds?",
                               "How should I take the Sleep Bundle?"])
def test_the_block_carries_the_directions_description_and_both_cautions_once(q):
    b = _block(q)
    section = b[b.index("### Sleep Bundle"):]
    assert DESCRIPTION in section
    assert f"Directions:\n{INTRO}" in section
    assert "Program directions and cautions" not in section
    for slug, name in (("brain-cleanse", "Brain Cleanse"), ("sleep-syntropy", "Sleep Syntropy")):
        assert b.count(_warning(slug)) == 1
        assert f"Caution for {name}: {_warning(slug)}" in section


def test_a_dose_question_gets_the_dose_rule_and_both_copy_rules():
    b = _block("How should I take the Sleep Bundle?")
    assert DOSE_RULE in b
    assert app._CAUTION_ONLY_NOTE not in b
    assert app._SEVERAL_CAUTIONS_RULE in b


def test_a_gated_answer_keeps_the_page_caution_wording():
    b = _block("How should I take the Sleep Bundle?", gated=True)
    assert app._GATED_FACTS_NOTE in b
    assert '"The caution on the product page for <product name> is:"' in b
    assert '"Caution for <product name>:"' not in b


def test_a_program_with_cautions_in_its_intro_keeps_its_label():
    b = _block("What is the Estrogen and Fibrin Balance Program?")
    assert "Program directions and cautions:\n" in b


# ---- the guidance hint ------------------------------------------------------------

@pytest.mark.parametrize("slug", [SLUG, "estrogen-fibrin-balance-program"])
def test_the_guidance_hint_ends_at_a_whole_sentence(slug):
    intro = re.sub(r"\s+", " ", PRODUCTS[slug]["intro"]).strip()
    assert len(intro) > 220
    purpose = app._product_guidance_hint(slug).split("| purpose: ", 1)[1]
    assert len(purpose) <= 220
    # A whole sentence, then the mark that more follows (review round 3).
    assert purpose.endswith(". ...")
    assert intro.startswith(purpose[:-len(" ...")])


def test_the_sleep_bundle_hint_never_ends_mid_dose():
    purpose = app._product_guidance_hint(SLUG).split("| purpose: ", 1)[1]
    assert purpose.endswith("within 30 minutes. ...")


def test_a_first_sentence_longer_than_the_limit_falls_back_to_a_word_cut(monkeypatch):
    products = copy.deepcopy(app._PRODUCTS)
    products["products"][SLUG]["intro"] = "word " * 80 + "end."
    monkeypatch.setattr(app, "_PRODUCTS", products)
    purpose = app._product_guidance_hint(SLUG).split("| purpose: ", 1)[1]
    assert purpose.endswith("...") and len(purpose) <= 220


@pytest.mark.parametrize("intro,bad_end", [
    # Review round 2: an abbreviation is not a sentence end, so the condition stays.
    ("Take 20 mg. only if directed by your clinician " + "and keep reading " * 15 + "end.",
     "20 mg."),
    # A decimal is not a sentence end, even where "1." ends exactly at the limit (216).
    ("A" * 150 + ". " + "B" * 56 + " take 1.5 mg daily. More text follows here.", "1."),
    # A dose unit before a capital: "20 mg. Only if ..." keeps its condition (round 2).
    ("Use it as directed and " + "more words " * 11 + "take 20 mg. Only if your clinician "
     + "advises it " * 8 + "end.", "mg."),
    # Past 60% of the limit, so the stub guard cannot mask it.
    ("Use it as directed and " + "more words " * 11 + "e.g. Brain Cleanse at bedtime "
     + "and more words " * 10 + "end.", "e.g."),
])
def test_the_hint_never_cuts_at_an_abbreviation_or_a_decimal(monkeypatch, intro, bad_end):
    products = copy.deepcopy(app._PRODUCTS)
    products["products"][SLUG]["intro"] = intro
    monkeypatch.setattr(app, "_PRODUCTS", products)
    purpose = app._product_guidance_hint(SLUG).split("| purpose: ", 1)[1]
    core = purpose.removesuffix(" ...").removesuffix("...")
    assert not core.endswith(bad_end), purpose
    assert len(purpose) <= 220


def test_a_short_first_sentence_is_not_kept_as_a_stub(monkeypatch):
    # Review round 3: scraped text such as "Vitality . <long sentence>" cut to "Vitality .".
    products = copy.deepcopy(app._PRODUCTS)
    products["products"][SLUG]["intro"] = "Vitality . Price: $70. " + "word " * 60 + "end."
    monkeypatch.setattr(app, "_PRODUCTS", products)
    purpose = app._product_guidance_hint(SLUG).split("| purpose: ", 1)[1]
    assert len(purpose) > 132 and purpose.endswith("...")
