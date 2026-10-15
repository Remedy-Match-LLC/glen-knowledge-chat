"""Brain Cleanse and Sleep Syntropy cautions and approved copy, on their pages and in the chatbot.

Glen approved both cautions word for word on 2026-10-15. Formulation specs, both released
on Glen's "send it": production/05 Formulations/_store-updates/
2026-10-15-brain-cleanse-caution.md and 2026-10-15-sleep-syntropy-caution.md. The page
adds its own "Caution: " heading, so the stored text has none.

Glen then approved each product's intro, description and directions, and two caution
changes for night-time use (formulation spec, sha 42d1b4fd: production/05 Formulations/
_store-updates/2026-10-15-brain-cleanse-sleep-syntropy-copy.md). All three sections are pinned.
"""
import copy
import importlib
import json
import sqlite3

import pytest

import app

BRAIN_CLEANSE = (
    "Causes drowsiness. Take only at bedtime. Do not drive or operate machinery "
    "after taking. Do not combine with alcohol, sedatives, or sleep aids other "
    "than Brain Cleanse and Sleep Syntropy. Avoid in "
    "pregnancy or while breastfeeding. Not for children. Do not use if you have "
    "liver disease. Stop use if you notice yellowing skin or eyes, or dark urine. "
    "If you take an antidepressant, a migraine medicine, tramadol, St John's wort "
    "or the cough suppressant dextromethorphan, consult your physician before use. "
    "If you have a thyroid condition, or take thyroid medicine, a blood thinner or "
    "blood sugar medicine, consult your physician before use. If you take blood "
    "pressure medicine or an immune-suppressing medicine, or have an autoimmune or "
    "hormone-sensitive condition, consult your physician before use. Stop a few "
    "days before surgery. If you are under medical treatment, consult your "
    "physician before use.")

SLEEP_SYNTROPY = (
    "May cause drowsiness. Take at bedtime, when wakeful during the night, or both, "
    "as desired or guided. Do not drive or operate machinery "
    "after taking. Do not combine with alcohol, sedatives, or sleep aids other "
    "than Brain Cleanse and Sleep Syntropy. Avoid in "
    "pregnancy or while breastfeeding. Not for children. Do not use if you have "
    "liver disease. Stop use if you notice yellowing skin or eyes, or dark urine. "
    "If you take an antidepressant, a migraine medicine, tramadol, St John's wort "
    "or the cough suppressant dextromethorphan, consult your physician before use. "
    "If you have a thyroid condition, or take thyroid medicine, a blood thinner or "
    "blood sugar medicine, consult your physician before use. If you take blood "
    "pressure medicine or an immune-suppressing medicine, or have an autoimmune or "
    "hormone-sensitive condition, consult your physician before use. This product "
    "contains CBD, which can change how the body processes some medicines. If you "
    "take a seizure medicine or any prescription medicine, consult your physician "
    "before use. Stop a few days before surgery. If you are under medical "
    "treatment, consult your physician before use.")

COPY = {
    "brain-cleanse": {
        "intro": "One capsule at bedtime, to support the cleansing of the brain and nervous "
                 "system while you sleep.",
        "description": "\n\n".join([
            "Brain Cleanse is a bedtime formula. It pairs 20 mg of melatonin with calming "
            "botanicals and amino acids.",
            "The brain clears the day's waste during deep sleep, through its own drainage "
            "network, the glymphatic system. Brain Cleanse is made to support the relaxation "
            "and deep sleep that this work depends on.",
            "Each capsule holds 5-HTP, GABA, ashwagandha root extract, honokiol, hops "
            "(xanthohumol) and bacosides from bacopa, with konjac glucomannan and xanthan gum.",
        ]),
        "directions": "1 capsule at bedtime. Turn the lights out within 30 minutes, and avoid "
                      "turning lights on during the night.",
    },
    "sleep-syntropy": {
        "intro": "A gentle formula to help you fall back to sleep in the middle of the night, "
                 "or to relax and fall asleep at bedtime.",
        "description": "\n\n".join([
            "Sleep Syntropy brings together nutritional and botanical factors that help relax "
            "the mind and nervous system for sleep. A small amount of melatonin, 250 mcg, "
            "supports the pineal gland's own rhythm.",
            "It leaves out valerian, which most herbal sleep remedies include. Some people feel "
            "more wakeful, not less, after valerian.",
            "Each capsule holds 5-HTP, GABA, L-theanine, magnesium L-threonate, ashwagandha root "
            "extract, lemon balm, passionflower, hops (xanthohumol), bacosides from bacopa, "
            "phellodendron, honokiol and CBD. Vitamin B1 as benfotiamine, active forms of B6, "
            "folate and B12, zinc and chromium round it out, along with a blend that aids "
            "absorption.",
            "Brain Cleanse carries a larger melatonin dose, 20 mg.",
        ]),
        "directions": "1 capsule in the middle of the night, to help you fall back to sleep. "
                      "You may also take 1 at bedtime, or at both times.",
    },
}
SECTIONS = ["intro", "description", "directions"]

PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))
APPROVED = {"brain-cleanse": BRAIN_CLEANSE, "sleep-syntropy": SLEEP_SYNTROPY}
NAMES = {"brain-cleanse": "Brain Cleanse", "sleep-syntropy": "Sleep Syntropy"}


# ---- the stored text --------------------------------------------------------------

@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_each_warning_is_the_approved_text_in_both_files(slug):
    assert PRODUCTS[slug]["warning"] == APPROVED[slug]
    assert CORRECTIONS[slug]["warning"] == APPROVED[slug]
    assert not APPROVED[slug].startswith("Caution")


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_each_opts_in_to_the_bot_and_pins_the_approved_copy(slug):
    for record in (PRODUCTS[slug], CORRECTIONS[slug]):
        assert record["chat_caution"] is True
        assert record["copy_pinned"] == SECTIONS
        for field in SECTIONS:
            assert record[field] == COPY[slug][field], (slug, field)


def test_the_caution_changes_for_night_time_use():
    # Sleep Syntropy may now be taken in the night; Brain Cleanse stays bedtime only.
    assert "Take only at bedtime." in BRAIN_CLEANSE
    assert "Take only at bedtime." not in SLEEP_SYNTROPY
    assert ("Take at bedtime, when wakeful during the night, or both, as desired or guided."
            in SLEEP_SYNTROPY)
    for text in (BRAIN_CLEANSE, SLEEP_SYNTROPY):
        assert "sleep aids or sedatives" not in text
        assert ("Do not combine with alcohol, sedatives, or sleep aids other than Brain "
                "Cleanse and Sleep Syntropy.") in text


def test_the_two_texts_differ_only_where_glen_made_them_differ():
    assert SLEEP_SYNTROPY.startswith("May cause drowsiness.")
    assert BRAIN_CLEANSE.startswith("Causes drowsiness.")
    assert "CBD" in SLEEP_SYNTROPY and "CBD" not in BRAIN_CLEANSE
    assert "If you have a thyroid condition, or take thyroid medicine" in BRAIN_CLEANSE
    assert "If you have a thyroid condition, or take thyroid medicine" in SLEEP_SYNTROPY


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_a_rebuild_restores_the_caution_and_keeps_the_real_source(slug):
    # Without the source in the correction, apply_corrections marks the formula
    # Glen-verified ("manual") and clears gk_stale, hiding the GrooveKart gap.
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    before = dict(products[slug])
    for field in ["warning", "chat_caution", "copy_pinned"] + SECTIONS:
        products[slug].pop(field)
    products[slug]["description"] = "SCRAPED STORE TEXT"
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[slug]["warning"] == APPROVED[slug]
    assert products[slug]["copy_pinned"] == SECTIONS
    for field in SECTIONS:
        assert products[slug][field] == COPY[slug][field], field
    assert products[slug]["chat_caution"] is True
    assert products[slug]["ingredients_source"] == "db-formulations-2026-06-25"
    assert products[slug].get("gk_stale") == before.get("gk_stale")


def test_a_correction_that_withdraws_the_opt_in_turns_it_off():
    # Review round 2: an explicit false must not leave an earlier true in place.
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    corrections = copy.deepcopy(CORRECTIONS)
    corrections["brain-cleanse"]["chat_caution"] = False
    apply_corrections(products, corrections)
    assert products["brain-cleanse"]["chat_caution"] is False


# ---- the pages --------------------------------------------------------------------

@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")
    return a


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_product_page_carries_its_caution_once(appmod, slug):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["ingredients"]["body"]["warning"] == APPROVED[slug]
    assert not any(s["id"].startswith("caution") for s in d["sections"])
    assert json.dumps(d).count(APPROVED[slug][:80]) == 1
    assert secs["ingredients"]["body"]["directions"] == COPY[slug]["directions"]
    assert secs["intro"]["body"] == COPY[slug]["intro"]
    assert secs["description"]["body"] == COPY[slug]["description"]
    assert secs["description"]["keep_breaks"] is True


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_a_cached_ai_draft_cannot_replace_the_intro_or_description(appmod, monkeypatch, slug):
    from dashboard import sales_pages as sp
    monkeypatch.setattr(appmod, "_SALES_AI_COPY_ENABLED", True)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for section in ("intro", "description"):
            sp.upsert_section(cx, slug, section, f"AI DRAFT MARKER {section}")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == COPY[slug]["intro"]
    assert secs["description"]["body"] == COPY[slug]["description"]
    assert "AI DRAFT MARKER" not in json.dumps(d)


# ---- the chatbot ------------------------------------------------------------------

def _block(q):
    return app.named_product_facts_block(q)


@pytest.mark.parametrize("q,slug", [
    ("What should I know before taking Brain Cleanse?", "brain-cleanse"),
    ("what should i know before taking brain cleanse?", "brain-cleanse"),
    ("What should I know before taking Sleep Syntropy?", "sleep-syntropy"),
    ("tell me about sleep syntropy", "sleep-syntropy"),      # case-free, no cue needed
])
def test_the_bot_gets_the_caution_exactly_once(q, slug):
    b = _block(q)
    assert f"### {NAMES[slug]}" in b, q
    assert b.count(APPROVED[slug]) == 1, q
    assert f"Caution: {APPROVED[slug]}" in b


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_block_holds_the_approved_description_directions_and_caution(slug):
    b = _block(f"What should I know before taking {NAMES[slug]}?")
    section = b[b.index("### "):]
    assert section == "\n".join([
        f"### {NAMES[slug]}", f"Page: {app._catalog_page_url(slug)}",
        COPY[slug]["description"],
        f"Directions: {COPY[slug]['directions']}",
        f"Caution: {APPROVED[slug]}"])
    assert app._CAUTION_ONLY_NOTE not in b


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_a_lone_pinned_caution_gets_the_copy_rule(slug):
    # Pinning ends caution-only status; the word-for-word rule must not go with it.
    b = _block(f"How much {NAMES[slug]} should I take?")
    assert app._ONE_CAUTION_RULE in b and app._SEVERAL_CAUTIONS_RULE not in b
    gated = app.named_product_facts_block(f"How much {NAMES[slug]} should I take?", gated=True)
    assert '"The label caution for <product name> is:"' in gated
    assert '"Caution for <product name>:"' not in gated


def test_both_pinned_cautions_get_the_several_rule():
    b = _block("Can I take Brain Cleanse and Sleep Syntropy together?")
    assert b.count(f"Caution: {BRAIN_CLEANSE}") == 1
    assert b.count(f"Caution: {SLEEP_SYNTROPY}") == 1
    assert app._SEVERAL_CAUTIONS_RULE in b
    # Gated, both cautions take the label wording (review round 1: this is what the
    # opt-in clause changes when two pinned cautions are already counted).
    gated = app.named_product_facts_block(
        "Can I take Brain Cleanse and Sleep Syntropy together?", gated=True)
    assert '"The label caution for <product name> is:"' in gated
    assert '"Caution for <product name>:"' not in gated


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_scraped_text_and_ingredients_stay_out(slug):
    b = _block(f"How much {NAMES[slug]} should I take?")
    for scraped in ("cleans your brain", "40%", "backfires"):
        assert scraped not in b, scraped
    assert "Ingredients from the label" not in b


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_guidance_hint_uses_the_approved_intro(slug):
    hint = app._product_guidance_hint(slug)
    assert f"purpose: {COPY[slug]['intro']}" in hint
    for scraped in ("cleans your brain", "40%", "backfires"):
        assert scraped not in hint, scraped


def test_lowercase_brain_cleanse_without_a_cue_stays_out():
    # "brain cleanse" is also an ordinary phrase: in lowercase it needs a product cue.
    assert "### Brain Cleanse" not in _block("how do i give my brain a cleanse")
    assert "### Brain Cleanse" not in _block("is a brain cleanse at night a real thing")


def test_the_old_name_sleep_synergy_is_a_known_gap():
    # The bot matches catalog names only (spec: known gap). Pinned here so a change
    # to alias matching is a decision, not an accident.
    assert "### Sleep Syntropy" not in _block("What is Sleep Synergy?")


def _synthetic(monkeypatch, **fields):
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["zorblax-test"] = dict(
        {"name": "Zorblax", "description": "SCRAPED CLAIM TEXT", "price_cents": 100}, **fields)
    monkeypatch.setattr(app, "_PRODUCTS", products)
    monkeypatch.setattr(app, "_CASE_FREE_NAMES", app._CASE_FREE_NAMES | {"zorblax-test"})


def test_a_pinned_product_with_the_opt_in_gets_the_caution_once(monkeypatch):
    _synthetic(monkeypatch, copy_pinned=["description"], chat_caution=True,
               warning="ZWARN text.", directions="ZDIR text.")
    b = _block("What is Zorblax?")
    assert b.count("ZWARN text.") == 1 and b.count("ZDIR text.") == 1
    assert "Only the caution below is approved text" not in b


def test_an_unpinned_opt_in_never_sends_directions_or_description(monkeypatch):
    _synthetic(monkeypatch, chat_caution=True, warning="ZWARN text.", directions="ZDIR text.")
    b = _block("What is Zorblax?")
    assert "ZWARN text." in b
    assert "ZDIR text." not in b and "SCRAPED CLAIM TEXT" not in b


def test_an_unpinned_product_without_the_opt_in_is_unchanged(monkeypatch):
    _synthetic(monkeypatch, warning="ZWARN text.", directions="ZDIR text.")
    assert "Zorblax" not in _block("What is Zorblax?")


def test_unpinned_products_with_directions_send_none():
    # Eleven unpinned products carry directions; none is approved for the bot (round 1).
    unpinned = [s for s, p in PRODUCTS.items()
                if isinstance(p, dict) and p.get("directions") and not p.get("copy_pinned")
                and not p.get("chat_caution")]
    assert unpinned
    for slug in unpinned:
        p = PRODUCTS[slug]
        b = _block(f"How much {p['name']} should I take?")
        assert (p["directions"] or "").strip() not in b, slug


# ---- the header for caution-only answers (formulation and review round 3) ----------

DOSE_RULE = "Give its dose from this text"


@pytest.mark.parametrize("q", [
    "Is the Sleep Bundle safe?",
    "how much brain program should i take?",
])
def test_a_caution_only_answer_asks_for_no_dose_or_description_from_the_text(q):
    b = _block(q)
    assert "### " in b, q
    assert DOSE_RULE not in b
    assert "Describe it only by what this text says" not in b
    assert "Answer directly from this text" not in b and "directly from this text" not in b
    assert "outranks every snippet about that caution" in b
    assert "Repeat its full caution, every sentence of it" in b


def test_the_gated_note_still_reaches_a_caution_only_answer():
    b = app.named_product_facts_block("Is the Sleep Bundle safe?", gated=True)
    assert app._GATED_FACTS_NOTE in b and DOSE_RULE not in b


def test_a_mixed_answer_keeps_the_full_header():
    # A pinned product's dose still comes from its text.
    b = _block("Is Brain Cleanse in the Sleep Bundle safe?")
    assert "### Brain Cleanse" in b and "### Sleep Bundle" in b
    assert DOSE_RULE in b
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1


def test_waiting_list_and_caution_only_gives_no_dose_and_the_full_caution(monkeypatch):
    # Formulation 2026-10-15: the waiting-list line wins, and the caution is still
    # repeated in full. No product is both today.
    _synthetic(monkeypatch, chat_caution=True, waitlist_only=True, warning="ZWARN text.")
    monkeypatch.setattr(app, "_waitlist_refusal",
                        lambda slug: "WAITLIST SENTENCE." if slug == "zorblax-test" else "")
    b = _block("How much Zorblax should I take?")
    assert "WAITLIST SENTENCE." in b
    assert "No dose or ingredient text is approved for it yet. Give none" in b
    assert app._CAUTION_ONLY_NOTE not in b
    assert b.count("Caution: ZWARN text.") == 1
    assert "repeat its full caution" in b.lower()
    # Outside review: the header must not ask for a dose the waiting-list line forbids.
    assert DOSE_RULE not in b
