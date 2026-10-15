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


@pytest.mark.parametrize("q,bundle", [
    ("Is the Sleep Bundle safe?", "sleep-bundle"),
    ("is sleep bundle safe with my meds?", "sleep-bundle"),
    ("What is in the Brain Program?", "brain-program"),
    ("how much brain program should i take?", "brain-program"),
])
def test_a_listed_bundle_gets_each_component_caution_by_name(q, bundle):
    b = _block(q)
    name = PRODUCTS[bundle]["name"]
    section = b[b.index("### "):]
    assert section.split("\n") == (
        [f"### {name}", f"Page: {app._catalog_page_url(bundle)}", NOTE]
        + [f"Caution for {NAMES[c]}: {APPROVED[c]}" for c in EXPECTED[bundle]]), q


def test_a_bundle_block_carries_no_description_or_components():
    b = _block("Is the Sleep Bundle safe?")
    p = PRODUCTS["sleep-bundle"]
    assert p["bundle_description"] not in b
    assert (p.get("description") or "x" * 40)[:40] not in b
    assert "Products in this program" not in b
    assert "Nightlight" not in b


def test_an_unlisted_bundle_gets_no_component_caution():
    b = _block("What is the Estrogen and Fibrin Balance Program?")
    assert "### Estrogen and Fibrin Balance Program" in b
    assert "Caution for Fibrosolve:" not in b and "Caution for Estro-Clear:" not in b


def test_a_component_without_the_bot_opt_in_is_left_out(monkeypatch):
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["sleep-syntropy"].pop("chat_caution")
    monkeypatch.setattr(app, "_PRODUCTS", products)
    b = _block("Is the Sleep Bundle safe?")
    assert BRAIN_CLEANSE in b and SLEEP_SYNTROPY not in b


def test_a_product_and_its_bundle_together_send_each_caution_once():
    b = _block("Is Brain Cleanse in the Sleep Bundle safe?")
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1
    assert "Caution: the Brain Cleanse caution is given in full above and applies here too." in b


def test_once_only_is_applied_after_the_product_cap(monkeypatch):
    # Four products named: the fourth is cut. A caution must not be withheld from a kept
    # block because a cut block would have carried it (round 3).
    assert app._NAMED_FACTS_MAX_PRODUCTS == 3
    b = _block("Angiostasis, Fibrosolve, Sleep Bundle and Brain Cleanse: which is safe?")
    assert "### Brain Cleanse" not in b               # the fourth block is cut
    assert b.count(BRAIN_CLEANSE) == 1 and b.count(SLEEP_SYNTROPY) == 1
    assert "given in full above" not in b
