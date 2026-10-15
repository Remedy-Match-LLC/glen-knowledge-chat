"""The Brain Cleanse caution: product page, both bundle pages and the chatbot.

Glen approved the wording on 2026-10-15, word for word. Formulation spec:
production/05 Formulations/_store-updates/2026-10-15-brain-cleanse-caution.md (released
on Glen's "send it", 2026-10-15). The page adds its own "Caution: " heading, so the stored
text has none. A change to this caution must change three records in two files.
"""
import copy
import importlib
import json

import pytest

import app

APPROVED = (
    "Causes drowsiness. Take only at bedtime. Do not drive or operate machinery "
    "after taking. Do not combine with alcohol, sleep aids or sedatives. Avoid in "
    "pregnancy or while breastfeeding. Not for children. Do not use if you have "
    "liver disease. Stop use if you notice yellowing skin or eyes, or dark urine. "
    "If you take an antidepressant, a migraine medicine, tramadol, St John's wort "
    "or the cough suppressant dextromethorphan, consult your physician before use. "
    "If you take thyroid medicine, a blood thinner or blood sugar medicine, consult "
    "your physician before use. If you take blood pressure medicine or an "
    "immune-suppressing medicine, or have an autoimmune or hormone-sensitive "
    "condition, consult your physician before use. Stop a few days before surgery. "
    "If you are under medical treatment, consult your physician before use.")

SLUGS = ("brain-cleanse", "sleep-bundle", "brain-program")
BUNDLES = ("sleep-bundle", "brain-program")
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))


# ---- the stored text --------------------------------------------------------------

def test_the_brain_cleanse_warning_is_the_approved_text():
    assert PRODUCTS["brain-cleanse"]["warning"] == APPROVED
    assert not APPROVED.startswith("Caution")


@pytest.mark.parametrize("slug", BUNDLES)
def test_each_bundle_warning_is_the_brain_cleanse_warning(slug):
    assert PRODUCTS[slug]["warning"] == PRODUCTS["brain-cleanse"]["warning"]


@pytest.mark.parametrize("slug", SLUGS)
def test_each_manual_correction_matches_the_catalog(slug):
    assert CORRECTIONS[slug]["warning"] == PRODUCTS[slug]["warning"]
    assert CORRECTIONS[slug]["chat_caution"] is True
    assert PRODUCTS[slug]["chat_caution"] is True


@pytest.mark.parametrize("slug", BUNDLES)
def test_both_bundles_still_contain_brain_cleanse(slug):
    comps = [c.get("slug") if isinstance(c, dict) else c
             for c in PRODUCTS[slug]["bundle_component_slugs"]]
    assert "brain-cleanse" in comps


@pytest.mark.parametrize("slug", SLUGS)
def test_nothing_else_is_pinned_or_given_directions(slug):
    p = PRODUCTS[slug]
    assert not p.get("copy_pinned")
    assert not p.get("directions")


def test_a_rebuild_restores_the_caution_and_keeps_the_real_source():
    # Without the source in the correction, apply_corrections marks the formula
    # Glen-verified ("manual") and clears gk_stale, hiding the GrooveKart gap (round 3).
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    for slug in SLUGS:
        products[slug].pop("warning")
        products[slug].pop("chat_caution")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    for slug in SLUGS:
        assert products[slug]["warning"] == APPROVED
        assert products[slug]["chat_caution"] is True
    bc = products["brain-cleanse"]
    assert bc["ingredients_source"] == "db-formulations-2026-06-25"
    assert bc.get("gk_stale") is True


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


def _page(appmod, slug):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    return d, {s["id"]: s for s in d["sections"]}


def test_the_product_page_carries_the_caution_once(appmod):
    d, secs = _page(appmod, "brain-cleanse")
    assert secs["ingredients"]["body"]["warning"] == APPROVED
    assert "caution" not in secs
    assert json.dumps(d).count(APPROVED[:60]) == 1


@pytest.mark.parametrize("slug", BUNDLES)
def test_each_bundle_page_has_its_own_caution_section(appmod, slug):
    d, secs = _page(appmod, slug)
    assert "ingredients" not in secs          # bundles have no ingredients
    sec = secs["caution"]
    assert sec["title"] == "Caution for Brain Cleanse"
    assert sec["body"] == APPROVED            # verbatim, no "Caution: " added
    assert json.dumps(d).count(APPROVED[:60]) == 1
    ids = [s["id"] for s in d["sections"]]
    assert ids.index("caution") < ids.index("cta")


def test_a_bundle_whose_caution_matches_no_component_is_titled_plainly(appmod, monkeypatch):
    p = dict(PRODUCTS["sleep-bundle"], warning="Some other caution.")
    assert appmod._bundle_caution_section(p)["title"] == "Caution"


def test_a_product_with_ingredients_gets_no_extra_section(appmod):
    p = dict(PRODUCTS["brain-cleanse"])
    assert appmod._bundle_caution_section(p) is None


# ---- the chatbot ------------------------------------------------------------------

def _block(q):
    return app.named_product_facts_block(q)


@pytest.mark.parametrize("q,name", [
    ("What should I know before taking Brain Cleanse?", "Brain Cleanse"),
    ("what should i know before taking brain cleanse?", "Brain Cleanse"),
    ("Is the Sleep Bundle safe?", "Sleep Bundle"),
    ("is sleep bundle safe with my meds?", "Sleep Bundle"),
    ("What is in the Brain Program?", "Brain Program"),
    ("how much brain program should i take?", "Brain Program"),
])
def test_the_bot_gets_the_caution_exactly_once(q, name):
    b = _block(q)
    assert f"### {name}" in b, q
    assert b.count(APPROVED) == 1, q
    assert f"Caution: {APPROVED}" in b


@pytest.mark.parametrize("q,slug", [
    ("What should I know before taking Brain Cleanse?", "brain-cleanse"),
    ("Is the Sleep Bundle safe?", "sleep-bundle"),
    ("What is in the Brain Program?", "brain-program"),
])
def test_the_block_holds_only_heading_link_and_caution(q, slug):
    b = _block(q)
    section = b[b.index("### "):]
    name = PRODUCTS[slug]["name"]
    assert section.split("\n") == [f"### {name}", f"Page: {app._catalog_page_url(slug)}",
                                   f"Caution: {APPROVED}"]


def test_the_scraped_description_and_ingredients_stay_out():
    b = _block("How much Brain Cleanse should I take?")
    desc = (PRODUCTS["brain-cleanse"].get("description") or "").strip()
    assert desc[:40] not in b
    assert "GABA" not in b and "Ingredients from the label" not in b
    assert "Directions:" not in b


def test_bundles_list_no_components_to_the_bot():
    b = _block("Is the Sleep Bundle safe?")
    assert "Products in this program" not in b
    assert "Sleep Syntropy" not in b


def test_lowercase_brain_cleanse_without_a_cue_stays_out():
    # "brain cleanse" is also an ordinary phrase: in lowercase it needs a product cue.
    assert "### Brain Cleanse" not in _block("how do i give my brain a cleanse")
    assert "### Brain Cleanse" not in _block("is a brain cleanse at night a real thing")


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


def test_an_unpinned_opt_in_never_sends_directions_or_description(monkeypatch):
    _synthetic(monkeypatch, chat_caution=True, warning="ZWARN text.", directions="ZDIR text.")
    b = _block("What is Zorblax?")
    assert "ZWARN text." in b
    assert "ZDIR text." not in b and "SCRAPED CLAIM TEXT" not in b


def test_an_unpinned_product_without_the_opt_in_is_unchanged(monkeypatch):
    _synthetic(monkeypatch, warning="ZWARN text.", directions="ZDIR text.")
    assert "Zorblax" not in _block("What is Zorblax?")


def test_unpinned_products_with_directions_send_none(monkeypatch):
    # Eleven unpinned products carry directions; none is approved for the bot (round 1).
    unpinned = [s for s, p in PRODUCTS.items()
                if isinstance(p, dict) and p.get("directions") and not p.get("copy_pinned")
                and not p.get("chat_caution")]
    assert unpinned
    for slug in unpinned:
        p = PRODUCTS[slug]
        b = _block(f"How much {p['name']} should I take?")
        assert (p["directions"] or "").strip() not in b, slug
