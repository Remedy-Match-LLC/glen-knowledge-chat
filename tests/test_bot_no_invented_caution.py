"""A pinned product with no caution says so, and the bot gets Glen's medicine answer.

Knowledge read-back, 2026-10-23: asked "Is Stress Release safe to take with my
medications?", the bot said "The caution on the product page is: this product contains
soy and hemp ...". Stress Release had no caution in the catalog, the code or Pinecone.
The gated note offered "The caution on the product page is ..." for every product, and
the model filled it in. Glen approved the answer to give instead ("yes", 2026-10-23).
Stress Release has its own caution since 2026-10-24, and Clarity since 2026-10-25.
"""
# Clarity carries its own caution since 2026-10-25 (Glen, platform tab), so these tests
# used Vitamin C Syntropy until it took the vinpocetine pregnancy line (2026-10-30). They
# now use Pterostilbene Pure Powder, a pinned product that still has none.
import importlib

import pytest

ANSWER = ("The product page carries no caution for Pterostilbene Pure Powder. Please ask Dr. Glen "
          "before combining it with any medicine.")
Q = "Is Pterostilbene Pure Powder safe to take with my medications?"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    return a


@pytest.mark.parametrize("gated", [False, True])
def test_a_product_with_no_caution_says_so_and_gives_the_medicine_answer(appmod, gated):
    block = appmod._named_product_facts(Q, gated=gated)
    assert "### Pterostilbene Pure Powder" in block
    assert "Pterostilbene Pure Powder has no caution on its product page." in block
    assert "Never quote, invent or attribute a caution to that page." in block
    assert f'say: "{ANSWER}"' in block
    assert "Caution:" not in block


def test_the_gated_note_offers_the_caution_sentence_only_where_one_is_given(appmod):
    note = appmod._GATED_FACTS_NOTE
    assert ("and, only where this text gives a caution, \"The caution on the product page "
            "is ...\"") in note


def test_a_product_with_a_caution_gets_no_such_line(appmod):
    block = appmod._named_product_facts("Is Vitamin D Syntropy safe with warfarin?")
    assert "### Vitamin D Syntropy" in block and "Caution:" in block
    assert "has no caution" not in block
    assert "Please ask Dr. Glen before combining" not in block


def test_a_bundle_is_never_said_to_have_no_caution(appmod):
    block = appmod._named_product_facts("How do I take the Estrogen and Fibrin Balance Program?")
    assert "### Estrogen and Fibrin Balance Program" in block
    assert "Estrogen and Fibrin Balance Program has no caution" not in block


def test_each_product_in_one_answer_gets_its_own_line(appmod):
    block = appmod._named_product_facts("Can I take Pterostilbene Pure Powder and Stamina Plus with my pills?")
    assert "Pterostilbene Pure Powder has no caution on its product page." in block
    assert "Stamina Plus has no caution on its product page." in block


def test_the_directions_still_stand_beside_the_line(appmod):
    block = appmod._named_product_facts(Q)
    assert "Its directions above still stand: repeat them as written." in block


def test_a_book_and_apparel_get_no_medicine_line(appmod):
    products = appmod._PRODUCTS["products"]
    for slug in ("book-dry-eye-relief", "mithreal-silver-t-shirt"):
        block = appmod._named_product_facts(f"Tell me about {products[slug]['name']}")
        assert "Please ask Dr. Glen before combining" not in block, slug


def test_a_pinned_supplement_with_no_ingredient_list_still_gets_it(appmod):
    block = appmod._named_product_facts("Is Stamina Plus safe with my medications?")
    assert "### Stamina Plus" in block
    assert "Stamina Plus has no caution on its product page." in block


def test_a_product_with_no_directions_is_not_asked_to_repeat_them(appmod):
    products = appmod._PRODUCTS["products"]
    p = products["lcarnosine"]
    assert p.get("copy_pinned") and not (p.get("directions") or "").strip()
    block = appmod._named_product_facts(f"Is {p['name']} safe with my medications?")
    assert f"{p['name']} has no caution on its product page." in block
    assert "directions above still stand" not in block


def test_beside_another_products_caution_the_line_keeps_its_own_answer(appmod):
    block = appmod._named_product_facts("Can I take Brain Cleanse and Pterostilbene Pure Powder with sertraline?")
    assert "### Brain Cleanse" in block and "Caution:" in block
    assert ("This line is about Pterostilbene Pure Powder alone. Another product's caution does not "
            "apply to Pterostilbene Pure Powder, and the rule to name no one else to ask does not cover "
            "Pterostilbene Pure Powder: still give this answer for it.") in block


@pytest.mark.parametrize("gated", [False, True])
def test_safety_pregnancy_and_conditions_get_the_ask_dr_glen_answer(appmod, gated):
    block = appmod._named_product_facts("Is Pterostilbene Pure Powder safe while pregnant?", gated=gated)
    assert ("If asked whether Pterostilbene Pure Powder is safe, or about taking it during pregnancy or "
            "nursing or with a health condition, say: \"The product page carries no caution "
            "for Pterostilbene Pure Powder. Please ask Dr. Glen before taking it.\"") in block
    assert "Never say or imply that no caution means it is safe." in block


def test_no_interaction_verdict_may_follow_the_line(appmod):
    block = appmod._named_product_facts("Can I take Pterostilbene Pure Powder with my blood pressure medicine?")
    assert ("After either answer, add nothing of your own about Pterostilbene Pure Powder with a medicine, "
            "in pregnancy or nursing, or with a health condition. Give no verdict on whether "
            "it interacts with or interferes with any medicine, no reassurance, no monitoring "
            "or dose advice, and nothing "
            "from the snippets about interactions or safety.") in block
    assert ("This limits only what you add yourself: still give its approved description, "
            "ingredients and directions when asked.") in block
