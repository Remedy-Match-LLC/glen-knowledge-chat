"""Bundle pages and the chatbot show listed component cautions, read from each component.

Formulation spec 2026-10-15-sleep-syntropy-caution.md, item 3 (replaces item 3 of the
Brain Cleanse spec). A bundle lists the components whose cautions it shows in
`caution_components`: an allowlist, so a warning added later to another component never
appears without Glen's yes. No caution text is copied onto a bundle.
"""
import copy
import importlib
import json

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

PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))
APPROVED = {"brain-cleanse": BRAIN_CLEANSE, "sleep-syntropy": SLEEP_SYNTROPY}
NAMES = {"brain-cleanse": "Brain Cleanse", "sleep-syntropy": "Sleep Syntropy"}

EXPECTED = {"sleep-bundle": ["brain-cleanse", "sleep-syntropy"],
            "brain-program": ["brain-cleanse"]}
UNLISTED = "estrogen-fibrin-balance-program"


# ---- the stored lists -------------------------------------------------------------

@pytest.mark.parametrize("bundle", sorted(EXPECTED))
def test_each_bundle_lists_exactly_its_components_in_both_files(bundle):
    assert PRODUCTS[bundle]["caution_components"] == EXPECTED[bundle]
    assert CORRECTIONS[bundle]["caution_components"] == EXPECTED[bundle]
    comps = [c.get("slug") if isinstance(c, dict) else c
             for c in PRODUCTS[bundle]["bundle_component_slugs"]]
    for slug in EXPECTED[bundle]:
        assert slug in comps
        assert PRODUCTS[slug]["warning"] == APPROVED[slug]


@pytest.mark.parametrize("bundle", sorted(EXPECTED))
def test_no_caution_text_is_copied_onto_a_bundle(bundle):
    for record in (PRODUCTS[bundle], CORRECTIONS[bundle]):
        assert "warning" not in record and "chat_caution" not in record


def test_only_the_two_bundles_carry_a_list():
    listed = {s for s, p in PRODUCTS.items() if isinstance(p, dict) and p.get("caution_components")}
    assert listed == set(EXPECTED)
    # Glen has not ruled on the fibrin program, though its components carry warnings.
    comps = [c.get("slug") if isinstance(c, dict) else c
             for c in PRODUCTS[UNLISTED]["bundle_component_slugs"]]
    assert any(PRODUCTS.get(c, {}).get("warning") for c in comps)


def test_a_rebuild_keeps_the_lists_and_the_bundle_source():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    for bundle in EXPECTED:
        products[bundle].pop("caution_components")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    for bundle in EXPECTED:
        assert products[bundle]["caution_components"] == EXPECTED[bundle]
        assert products[bundle]["ingredients_source"] == PRODUCTS[bundle]["ingredients_source"]
        assert products[bundle]["ingredients_source"] != "manual"


def test_an_emptied_list_replays_as_empty():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    corrections = copy.deepcopy(CORRECTIONS)
    corrections["sleep-bundle"]["caution_components"] = []
    apply_corrections(products, corrections)
    assert products["sleep-bundle"]["caution_components"] == []


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


def _page(a, slug):
    return a.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()


@pytest.mark.parametrize("bundle", sorted(EXPECTED))
def test_a_listed_bundle_shows_one_section_per_component(appmod, bundle):
    d = _page(appmod, bundle)
    ids = [s["id"] for s in d["sections"]]
    cautions = [s for s in d["sections"] if s["id"].startswith("caution")]
    assert [s["id"] for s in cautions] == [f"caution-{c}" for c in EXPECTED[bundle]]
    for sec, slug in zip(cautions, EXPECTED[bundle]):
        assert sec["title"] == f"Caution for {NAMES[slug]}"
        assert sec["body"] == APPROVED[slug]           # as stored, no "Caution: " added
        assert json.dumps(d).count(APPROVED[slug][:80]) == 1
        assert ids.index(sec["id"]) < ids.index("cta")
    assert "caution" not in ids                        # the old single section is gone
    assert "ingredients" not in ids                    # bundles have no ingredients


def test_an_unlisted_bundle_shows_no_caution_section(appmod):
    d = _page(appmod, UNLISTED)
    assert not any(s["id"].startswith("caution") for s in d["sections"])


def test_no_bundle_page_has_the_old_caution_section(appmod):
    for slug, p in PRODUCTS.items():
        if isinstance(p, dict) and p.get("bundle") and p.get("caution_components"):
            assert "caution" not in [s["id"] for s in _page(appmod, slug)["sections"]]


def test_a_retired_component_resolves_to_its_survivor_once(appmod, monkeypatch):
    products = copy.deepcopy(appmod._PRODUCTS)
    products["products"]["old-cleanse"] = {"name": "Old Cleanse", "inactive": True,
                                           "superseded_by": "brain-cleanse",
                                           "warning": "OLD TEXT"}
    products["products"]["gone"] = {"name": "Gone", "inactive": True, "warning": "GONE"}
    b = dict(products["products"]["brain-program"],
             caution_components=["old-cleanse", "brain-cleanse", "gone"])
    monkeypatch.setattr(appmod, "_PRODUCTS", products)
    secs = appmod._bundle_caution_sections(b)
    assert [(s["id"], s["title"], s["body"]) for s in secs] == [
        ("caution-brain-cleanse", "Caution for Brain Cleanse", BRAIN_CLEANSE)]


# ---- the chatbot ------------------------------------------------------------------

def _block(q):
    return app.named_product_facts_block(q)


NOTE = ("Only the caution below is approved text. Repeat it in full. "
        "For anything else, answer from the snippets as usual.")


# The Sleep Bundle is pinned since its copy spec (2026-10-15), so its block carries its
# approved text too: tests/test_sleep_bundle_copy.py. The caution-only shape is the
# Brain Program's.
@pytest.mark.parametrize("q,bundle", [
    ("What is in the Brain Program?", "brain-program"),
    ("how much brain program should i take?", "brain-program"),
])
def test_a_listed_bundle_gets_each_component_caution_by_name(q, bundle):
    b = _block(q)
    name = PRODUCTS[bundle]["name"]
    section = b[b.index("### "):]
    assert section.split("\n") == (
        [f"### {name}", f"Page: {app._catalog_page_url(bundle)}", NOTE]
        + [line for c in EXPECTED[bundle]
           for line in (f"Caution for {NAMES[c]}: {APPROVED[c]}", app._CAUTION_STAY_LINE)]
        # The closing reminder ends the block (cautionadd, 2026-10-21).
        + ["", app._CAUTION_CLOSING_REMINDER]), q


def test_an_unpinned_bundle_block_carries_no_description_or_components():
    b = _block("Is the Brain Program safe?")
    assert "### Brain Program" in b
    # The Brain Program has no description or bundle_description of its own, so the
    # block's shape is the check (review round 3): no component list, no directions.
    assert "Products in this program" not in b
    assert "Directions:" not in b and "Program directions and cautions" not in b


def test_an_unlisted_bundle_gets_no_component_caution():
    b = _block("What is the Estrogen and Fibrin Balance Program?")
    assert "### Estrogen and Fibrin Balance Program" in b
    assert "Caution for Fibrosolve:" not in b and "Caution for Estro-Clear:" not in b


def test_a_component_without_the_bot_opt_in_is_left_out(monkeypatch):
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["sleep-syntropy"].pop("chat_caution")
    monkeypatch.setattr(app, "_PRODUCTS", products)
    # A caution question now gets the link (2026-10-24); the copy path runs on this one.
    b = _block("What is in the Sleep Bundle?")
    assert BRAIN_CLEANSE in b and SLEEP_SYNTROPY not in b


def test_a_product_and_its_bundle_together_send_each_caution_once():
    # A caution question now gets the link (2026-10-24); the copy path runs on this one.
    b = _block("What does Brain Cleanse in the Sleep Bundle do?")
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1
    assert "Caution: the Brain Cleanse caution is given in full above and applies here too." in b


def test_four_names_keep_three_blocks_and_each_caution_once(monkeypatch):
    # Four products named: the fourth is cut, and each kept caution appears once (spec
    # round 3). The loop stops before building a fourth block, so this pins the outcome;
    # it cannot tell a dedupe before the cap from one after it (review round 1).
    assert app._NAMED_FACTS_MAX_PRODUCTS == 3
    # A caution question now gets the link (2026-10-24); the copy path runs on this one.
    b = _block("Angiostasis, Fibrosolve, Sleep Bundle and Brain Cleanse: which is best?")
    assert "### Brain Cleanse" not in b               # the fourth block is cut
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1
    assert "given in full above" not in b


def test_a_pinned_description_holding_its_caution_counts_as_given(monkeypatch):
    # Review round 2: the caution inside a pinned description must count, or a bundle
    # named after it sends the text a second time.
    products = copy.deepcopy(app._PRODUCTS)
    bc = products["products"]["brain-cleanse"]
    bc["copy_pinned"] = ["description"]
    bc["description"] = "Approved text. " + BRAIN_CLEANSE
    monkeypatch.setattr(app, "_PRODUCTS", products)
    # A caution question now gets the link (2026-10-24); the copy path runs on this one.
    b = _block("What does Brain Cleanse in the Sleep Bundle do?")
    assert b.count(BRAIN_CLEANSE) == 1


@pytest.mark.parametrize("bundle", sorted(EXPECTED))
def test_no_listed_component_carries_its_caution_in_a_pinned_description(bundle):
    # The reverse order (bundle named first) cannot be deduplicated against a
    # description, so the data must not create that case without a decision.
    for slug in EXPECTED[bundle]:
        p = PRODUCTS[slug]
        assert not (p.get("copy_pinned") and p["warning"] in (p.get("description") or ""))


def test_a_listed_bundle_with_no_opted_in_component_is_not_admitted(monkeypatch):
    # Review round 1: it would outrank other names with an empty block.
    # The Brain Program: a pinned Sleep Bundle is admitted on copy_pinned instead.
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["brain-cleanse"].pop("chat_caution")
    monkeypatch.setattr(app, "_PRODUCTS", products)
    assert "### Brain Program" not in _block("Is the Brain Program safe?")
    # The empty block is dropped anyway; the match itself is what must not happen.
    spans = app._named_product_spans("Is the Brain Program safe?", products["products"])
    assert "brain-program" not in spans


def test_a_listed_component_with_no_warning_does_not_admit_the_bundle(monkeypatch):
    # Review round 3: the opt-in alone is not enough; the bundle would match with an
    # empty block.
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["brain-cleanse"]["warning"] = ""
    monkeypatch.setattr(app, "_PRODUCTS", products)
    assert "brain-program" not in app._named_product_spans(
        "Is the Brain Program safe?", products["products"])


def test_the_page_follows_the_bundle_list_not_the_bot_opt_in(appmod, monkeypatch):
    # Review round 3: withdrawing a component's bot opt-in must not drop it from the page.
    products = copy.deepcopy(appmod._PRODUCTS)
    products["products"]["sleep-syntropy"].pop("chat_caution")
    monkeypatch.setattr(appmod, "_PRODUCTS", products)
    ids = [s["id"] for s in appmod._bundle_caution_sections(products["products"]["sleep-bundle"])]
    assert ids == ["caution-brain-cleanse", "caution-sleep-syntropy"]


# ---- formulation read-back, 2026-10-15 -------------------------------------------

@pytest.mark.parametrize("q,name", [
    ("what should i know before taking the sleep bundle?", "Sleep Bundle"),
    ("What should I know before taking the Sleep Bundle?", "Sleep Bundle"),
    ("how much of the brain program should i take?", "Brain Program"),
])
def test_the_before_a_bundle_name_still_matches(q, name):
    b = _block(q)
    headings = [l for l in b.split("\n") if l.startswith("### ")]
    assert headings == [f"### {name}"], q


def test_the_still_blocks_a_one_word_lowercase_name():
    # "what is the appestat?" asks about the body's set point (review 2026-10-09).
    assert "### Appestat" not in _block("what is the appestat?")


@pytest.mark.parametrize("q", [
    "What should I know before taking the Sleep Bundle?",
    "what should i know before taking the sleep bundle?",
])
def test_two_cautions_get_the_keep_them_separate_rule(q):
    b = _block(q)
    assert app._SEVERAL_CAUTIONS_RULE in b
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1


@pytest.mark.parametrize("q", [
    "What should I know before taking the Brain Program?",
    "What should I know before taking Brain Cleanse?",
    "what should i know before taking brain cleanse?",
    "What should I know before taking Sleep Syntropy?",
])
def test_one_opted_in_caution_gets_the_word_for_word_rule(q):
    # Read-back on 17ba8a1e: a lone caution was paraphrased and lost most lines. Brain
    # Cleanse and Sleep Syntropy are pinned since the copy spec (2026-10-15) and keep the
    # rule through their chat_caution opt-in; the Brain Program is still caution-only.
    b = _block(q)
    assert app._ONE_CAUTION_RULE in b, q
    assert app._SEVERAL_CAUTIONS_RULE not in b


@pytest.mark.parametrize("q", [
    "What is Estro-Clear?",
    "How much Estro-Clear should I take?",
])
@pytest.mark.parametrize("gated", [False, True])
def test_a_pinned_product_alone_keeps_its_header_unchanged(q, gated, monkeypatch):
    # Byte for byte: with both rules switched off entirely, the block is the same.
    # Estro-Clear opts in since 2026-10-21, so it is switched off for this path.
    monkeypatch.setitem(app._PRODUCTS["products"]["estro-clear"], "chat_caution", False)
    _block = lambda q: app.named_product_facts_block(q, gated=gated)  # noqa: E731
    b = _block(q)
    assert "### " in b and "Caution: " in b, q
    monkeypatch.setattr(app, "_SEVERAL_CAUTIONS_RULE", "SHOULD NOT APPEAR")
    monkeypatch.setattr(app, "_ONE_CAUTION_RULE", "SHOULD NOT APPEAR")
    assert _block(q) == b


@pytest.mark.parametrize("q", [
    "What should I know before taking Brain Cleanse?",
    "What should I know before taking the Sleep Bundle?",
])
def test_a_gated_answer_asks_for_the_label_caution_wording(q):
    # Review round 1: the gated note asks for the page caution wording. Brain Cleanse
    # keeps the rewording through its opt-in after pinning (copy spec, 2026-10-15).
    b = app.named_product_facts_block(q, gated=True)
    assert app._GATED_FACTS_NOTE in b
    assert '"The caution on the product page for <product name> is:"' in b
    assert '"Caution for <product name>:"' not in b


def test_a_gated_pinned_only_answer_with_several_cautions_is_unchanged(monkeypatch):
    # Review round 3: the gated rewording belongs to answers with an opted-in caution.
    # All three opt in since 2026-10-21, so they are switched off for this path.
    for slug in ("vitamin-a-syntropy", "estro-clear", "phytolacca-americana-oil-roll-on"):
        monkeypatch.setitem(app._PRODUCTS["products"][slug], "chat_caution", False)
    q = "Vitamin A Syntropy, Estro-Clear and Phytolacca americana Oil Roll-On?"
    b = app.named_product_facts_block(q, gated=True)
    assert app._SEVERAL_CAUTIONS_RULE in b
    assert '"The caution on the product page for <product name> is:"' not in b
    # Positive check, so a revert of the rewording cannot pass silently (review round 1).
    assert '"Caution for <product name>:"' in b


def test_a_caution_inside_a_pinned_description_counts_beside_an_opted_in_caution():
    # Review round 3: AngiogenX carries its caution in its pinned description, so with
    # Brain Cleanse the answer shows two cautions and needs the plural rule. Brain Cleanse
    # is pinned with the opt-in since the copy spec (2026-10-15); the count still holds.
    b = _block("What is Brain Cleanse and AngiogenX?")
    assert "### AngiogenX" in b and "### Brain Cleanse" in b
    assert app._SEVERAL_CAUTIONS_RULE in b and app._ONE_CAUTION_RULE not in b
