"""Brain Cleanse and Sleep Syntropy cautions on their own pages and in the chatbot.

Glen approved both cautions word for word on 2026-10-15. Formulation specs, both released
on Glen's "send it": production/05 Formulations/_store-updates/
2026-10-15-brain-cleanse-caution.md and 2026-10-15-sleep-syntropy-caution.md. The page
adds its own "Caution: " heading, so the stored text has none.
"""
import copy
import importlib
import json

import pytest

import app

BRAIN_CLEANSE = (
    "Causes drowsiness. Take only at bedtime. Do not drive or operate machinery "
    "after taking. Do not combine with alcohol, sleep aids or sedatives. Avoid in "
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
    "May cause drowsiness. Take only at bedtime. Do not drive or operate machinery "
    "after taking. Do not combine with alcohol, sleep aids or sedatives. Avoid in "
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
def test_each_opts_in_to_the_bot_without_pinning_anything(slug):
    for record in (PRODUCTS[slug], CORRECTIONS[slug]):
        assert record["chat_caution"] is True
    assert not PRODUCTS[slug].get("copy_pinned")
    assert not PRODUCTS[slug].get("directions")


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
    products[slug].pop("warning")
    products[slug].pop("chat_caution")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[slug]["warning"] == APPROVED[slug]
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
def test_the_block_holds_only_heading_link_note_and_caution(slug):
    b = _block(f"What should I know before taking {NAMES[slug]}?")
    section = b[b.index("### "):]
    assert section.split("\n") == [
        f"### {NAMES[slug]}", f"Page: {app._catalog_page_url(slug)}",
        "Only the caution below is approved text. Repeat it in full. "
        "For anything else, answer from the snippets as usual.",
        f"Caution: {APPROVED[slug]}"]


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_scraped_description_and_ingredients_stay_out(slug):
    b = _block(f"How much {NAMES[slug]} should I take?")
    desc = (PRODUCTS[slug].get("description") or "").strip()
    assert desc[:40] not in b
    assert "Ingredients from the label" not in b and "Directions:" not in b


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
