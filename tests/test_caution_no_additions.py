"""The bot copies a caution and adds nothing around it.

Live chat on 81acb916, 2026-10-21. Asked "Can I take Vitamin D Syntropy if I am on
warfarin?", the bot quoted only the first sentence of the caution and called it the
label's. It said some patients tolerate it with monitoring, said to adjust the warfarin
dose, and suggested a different vitamin D source. Vitamin D Syntropy was pinned but had no
chat_caution, so no copy rule reached the bot for it. The copy rules also never forbade
framing or advice around a caution.
"""
import copy
import json

import pytest

import app

SLUG = "vitamin-d-syntropy"
WARNING = ("If you take a blood thinner such as warfarin, consult your physician before "
           "use, because vitamin K affects it. Consult your physician before use if you "
           "are pregnant or have a thyroid condition.")
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))
RULES = ("_ONE_CAUTION_RULE", "_SEVERAL_CAUTIONS_RULE", "_CAUTION_COPY_REMINDER")
NO_ADDITIONS = (
    'Call each caution "the caution on the product page", never the label caution.',
    "Add no advice of your own around a caution.",
    "Do not say how well clients tolerate the product.",
    "Do not suggest monitoring, or changing any medicine or dose.",
    "Name no other product or source in place of the product.",
    "For anything beyond the caution, say to ask Dr. Glen.",
)


def test_vitamin_d_syntropy_opts_its_caution_into_the_chat_in_both_files():
    for record in (PRODUCTS[SLUG], CORRECTIONS[SLUG]):
        assert record["chat_caution"] is True
        assert record["warning"] == WARNING


def test_a_rebuild_keeps_the_opt_in():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    products[SLUG].pop("chat_caution")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[SLUG]["chat_caution"] is True


@pytest.mark.parametrize("q", ["Can I take Vitamin D Syntropy if I am on warfarin?",
                               "Is Vitamin D Syntropy safe with a blood thinner?"])
def test_a_warfarin_question_gets_the_full_caution_and_the_copy_rule(q):
    b = app.named_product_facts_block(q)
    assert "### Vitamin D Syntropy" in b
    # Both sentences, once: the second one was dropped live.
    assert b.count(WARNING) == 1
    assert app._ONE_CAUTION_RULE in b
    assert app._SEVERAL_CAUTIONS_RULE not in b


@pytest.mark.parametrize("rule", RULES)
def test_each_copy_rule_forbids_additions_around_the_caution(rule):
    text = getattr(app, rule)
    for sentence in NO_ADDITIONS:
        assert sentence in text, (rule, sentence)
    assert "patient" not in text.lower()
    assert "—" not in text


def test_the_gated_rewording_still_finds_its_target():
    # The gated answer swaps the paragraph start; the added sentences must not break it.
    b = app.named_product_facts_block("Can I take Vitamin D Syntropy if I am on warfarin?",
                                      gated=True)
    assert '"The caution on the product page for <product name> is:"' in b
    assert b.count(WARNING) == 1
