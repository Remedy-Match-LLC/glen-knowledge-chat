"""Scar Silk's caution moves from the label to the store page.

Glen approved the caution on 2026-10-09 and the move on 2026-10-15 (production spec
production/05 Formulations/_store-updates/2026-10-15-scar-silk-caution.md). It is not
pinned. Glen then said yes to the chatbot carrying it (the spec's addendum, 2026-10-15), so
it opts in with chat_caution and the bot block carries the warning alone, word for word.
"""
import copy
import importlib
import json

import pytest

import app

APPROVED = ("Ask your practitioner before use if you take a blood thinner or prescription "
            "medication, or before surgery.")
DIRECTIONS = "Up to 3 capsules a day between meals."
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))


def test_the_warning_is_the_approved_text_in_both_files():
    assert PRODUCTS["scar-silk"]["warning"] == APPROVED
    assert CORRECTIONS["scar-silk"]["warning"] == APPROVED


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
    return dict(pairs)


def test_neither_file_holds_a_second_scar_silk_entry():
    # Review round 2: json.load keeps only the last of a repeated key.
    for path in ("data/products.json", "data/products-manual-corrections.json"):
        json.load(open(path), object_pairs_hook=_no_duplicate_keys)


def test_no_other_field_already_carries_the_caution():
    # The page could show it twice if the description or intro held it too.
    p = PRODUCTS["scar-silk"]
    for field in ("description", "intro", "panel_note", "directions"):
        assert APPROVED not in (p.get(field) or ""), field


def test_only_the_caution_and_the_directions_reach_the_chatbot():
    # Addendum 2 (Glen "yes", 2026-10-15): the label directions, pinned alone.
    for record in (PRODUCTS["scar-silk"], CORRECTIONS["scar-silk"]):
        assert record["chat_caution"] is True
        assert record["directions"] == DIRECTIONS
        assert record["copy_pinned"] == ["directions"]


def test_the_page_gives_the_directions(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/scar-silk").get_json()
    assert json.dumps(d).count(DIRECTIONS) >= 1


def test_a_rebuild_keeps_the_caution_and_the_real_source():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    products["scar-silk"].pop("warning")
    products["scar-silk"].pop("chat_caution")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products["scar-silk"]["warning"] == APPROVED
    assert products["scar-silk"]["chat_caution"] is True
    assert products["scar-silk"]["ingredients_source"] == "db-formulations-2026-06-25"


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


def test_the_page_carries_the_caution_once(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/scar-silk").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["ingredients"]["body"]["warning"] == APPROVED
    assert json.dumps(d).count(APPROVED) == 1


@pytest.mark.parametrize("q", ["Is Scar Silk safe?", "What is Scar Silk?",
                               "How much Scar Silk should I take?",
                               "Is Scar Silk safe if I take a blood thinner?"])
def test_the_chatbot_carries_the_directions_and_the_caution_once(q):
    b = app.named_product_facts_block(q)
    section = b[b.index("### Scar Silk"):]
    # No description: it is not approved, and only a pinned description reaches the bot.
    assert section.split("\n") == [
        "### Scar Silk", f"Page: {app._catalog_page_url('scar-silk')}",
        f"Directions: {DIRECTIONS}",
        app._PINNED_DOSE_RULE.format(name="Scar Silk", example=""),
        app._CAUTION_COPY_REMINDER, f"Caution: {APPROVED}"]
    assert b.count(APPROVED) == 1
    assert app._ONE_CAUTION_RULE in b
    # The sleep products' example could forbid two at once, which up to 3 a day allows.
    assert "two capsules at one time" not in b


def test_the_gated_note_names_the_product_page_not_the_label():
    # Labels carry no cautions (Glen, 2026-10-15), so the caution is read back as the
    # product page's (Glen "yes", 2026-10-15). Directions keep the label wording.
    assert "label caution" not in app._GATED_FACTS_NOTE.lower()
    assert '"The caution on the product page is ..."' in app._GATED_FACTS_NOTE
    assert '"The label directions are ..."' in app._GATED_FACTS_NOTE


def test_a_gated_answer_gets_the_product_page_wording():
    b = app.named_product_facts_block("Is Scar Silk safe if I take a blood thinner?", gated=True)
    assert app._GATED_FACTS_NOTE in b and b.count(APPROVED) == 1
    assert '"The caution on the product page for <product name> is:"' in b


def test_lowercase_scar_silk_without_a_cue_is_a_known_gap():
    # Lowercase names match only with a product cue unless listed as case-free. The spec
    # changes nothing else, so "is scar silk safe?" gets no block. Pinned here so a change
    # is a decision, not an accident.
    assert "### Scar Silk" not in app.named_product_facts_block("is scar silk safe?")
