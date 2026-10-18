"""Iron Out: pinned directions and a caution, up to 4 a day.

Glen, 2026-10-18: "change the dosage range to up to 4 a day", "yes" to the directions
line below, and he wrote the caution: "Take up to 4 capsules a day." Spec, production
pillar: 05 Formulations/_store-updates/2026-10-18-iron-out-directions.md.

The live bot had told a buyer "up to 15 in acute cases". Each capsule holds 100 mcg
selenium, so 15 is 1,500 mcg against Glen's 400 mcg limit.
"""
import importlib
import json

import pytest

import app

DIRECTIONS = "1 to 4 capsules daily with food, or as guided."
CAUTION = "Take up to 4 capsules a day."
FIELDS = {"directions": DIRECTIONS, "copy_pinned": ["directions"], "warning": CAUTION,
          "chat_caution": True}
QUESTIONS = ["How many Iron Out capsules can I take a day?",
             "how many iron out capsules can I take a day?",
             "iron out dose?",
             "how much iron out should I take"]


def _repo(path):
    """The repo file itself, never via DATA_DIR, which other tests repoint."""
    return json.load(open(path))


def test_the_four_fields_are_the_approved_text_in_both_files():
    products = _repo("data/products.json")["products"]["iron-out"]
    corrections = _repo("data/products-manual-corrections.json")["iron-out"]
    for record in (products, corrections):
        assert {k: record.get(k) for k in FIELDS} == FIELDS


def test_the_correction_holds_only_the_four_fields_and_its_note():
    assert set(_repo("data/products-manual-corrections.json")["iron-out"]) == \
        set(FIELDS) | {"note"}


@pytest.mark.parametrize("question", QUESTIONS)
def test_the_chat_block_holds_the_directions_the_caution_and_the_rule(question):
    block = app.named_product_facts_block(question)
    assert "### Iron Out" in block, question
    section = block[block.index("### Iron Out"):]
    assert f"Directions: {DIRECTIONS}" in section
    assert section.count(CAUTION) == 1
    assert f"Caution: {CAUTION}" in section
    assert app._PINNED_DOSE_RULE.format(name="Iron Out", example="") in section
    # No description is pinned, so none is sent.
    assert app._NO_DESCRIPTION.format(name="Iron Out") in section


def test_no_higher_count_reaches_the_block():
    section = app.named_product_facts_block(QUESTIONS[0])
    section = section[section.index("### Iron Out"):]
    for n in ("6", "8", "15"):
        assert f" {n} " not in f" {section} ".replace(".", " ").replace(",", " "), n


def test_the_everyday_phrase_gets_no_block():
    assert "### Iron Out" not in app.named_product_facts_block(
        "we need to iron out the details")


@pytest.mark.parametrize("question", ["can I take more iron out?",
                                      "How many Iron-Out capsules can I take?",
                                      "iron-out dose?"])
def test_known_gaps_get_no_block(question):
    """Listed in the PR, not widened here (spec). A change that closes one fails this."""
    assert "### Iron Out" not in app.named_product_facts_block(question)


def test_a_rebuild_keeps_the_four_fields():
    import copy
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(_repo("data/products.json")["products"])
    for k in FIELDS:
        products["iron-out"].pop(k)
    apply_corrections(products, copy.deepcopy(_repo("data/products-manual-corrections.json")))
    assert {k: products["iron-out"].get(k) for k in FIELDS} == FIELDS


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


def test_the_page_gives_the_directions_and_the_caution(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/iron-out").get_json()
    body = {s["id"]: s for s in d["sections"]}["ingredients"]["body"]
    assert body["directions"] == DIRECTIONS
    assert body["warning"] == CAUTION
