"""The bot copies a caution and adds nothing around it.

Live chat on 81acb916, 2026-10-21. Asked "Can I take Vitamin D Syntropy if I am on
warfarin?", the bot quoted only the first sentence of the caution and called it the
label's. It said some patients tolerate it with monitoring, said to adjust the warfarin
dose, and suggested a different vitamin D source. Vitamin D Syntropy was pinned but had no
chat_caution, so no copy rule reached the bot for it. The copy rules also never forbade
framing or advice around a caution.

Review round 1 (2026-10-21) scoped the rule to the caution's own subject, removed the
destination it named, and wrote it once per answer in the header.
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
DESCRIPTION_LEAD = "Vitamin D is formed in the skin when sunlight transforms cholesterol"
MONITORING = ("Individuals who take more than 2,000 IU per day of vitamin D should have "
              "their vitamin D levels monitored.")
RULE = app._CAUTION_NO_ADDITIONS


def _header(b):
    return b[:b.index("\n### ")]


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
def test_a_warfarin_question_gets_the_full_caution_and_the_rules_once(q):
    b = app.named_product_facts_block(q)
    assert "### Vitamin D Syntropy" in b
    # Both sentences, once: the second one was dropped live. The caution is held inside
    # the pinned description, so that paragraph must be there to carry it.
    assert b.count(WARNING) == 1 and DESCRIPTION_LEAD in b
    assert app._ONE_CAUTION_RULE in _header(b) and app._SEVERAL_CAUTIONS_RULE not in b
    assert b.count(RULE) == 1 and RULE in _header(b)
    assert "the caution on the product page" in RULE and "never the label caution" in RULE
    for added in ("tolerate the product", "monitoring or changing any medicine or dose",
                  "no one else to ask"):
        assert added in RULE
    # No destination of the bot's own: the caution's own "consult your physician" stands.
    assert "Dr. Glen" not in b
    assert "patient" not in b.lower() and "\u2014" not in RULE


def test_a_dose_question_keeps_the_approved_monitoring_line():
    # Review round 1, F1: the description's own monitoring line is approved text, so the
    # rule must carve it out rather than ban monitoring everywhere.
    b = app.named_product_facts_block("How much Vitamin D Syntropy should I take?")
    assert MONITORING in b and b.count(RULE) == 1
    assert "Repeat the approved description and directions below as written" in RULE
    assert "When the user asks about a medicine, pregnancy or a condition that a caution names" in RULE


def test_the_brain_program_note_and_the_rule_agree():
    # Review round 1, F2: the caution-only note says to answer from the snippets. The rule
    # limits only the caution's subject, so it leaves other questions to that note.
    b = app.named_product_facts_block(
        "What is the Brain Program and is it safe with my antidepressant?")
    assert app._CAUTION_ONLY_NOTE in b and b.count(RULE) == 1
    assert "answer other questions as before" in RULE
    assert "This limits only what you add yourself." in RULE


def test_two_opted_in_cautions_still_carry_the_rule_once():
    # Review round 1, F5: each pinned block keeps its own reminder, the rule is not repeated.
    b = app.named_product_facts_block("Is Brain Cleanse safe with Sleep Syntropy?")
    assert app._SEVERAL_CAUTIONS_RULE in b and b.count(RULE) == 1
    assert b.count(app._CAUTION_COPY_REMINDER) == 2
    assert RULE not in app._CAUTION_COPY_REMINDER


def test_a_product_without_the_opt_in_keeps_its_header():
    # Estro-Clear is pinned with a warning but never opted in: no copy rule, no new rule.
    b = app.named_product_facts_block("Is Estro-Clear safe?")
    assert "### Estro-Clear" in b and RULE not in b


def test_the_gated_rewording_still_finds_its_target():
    # The gated answer swaps the paragraph start; the added sentences must not break it.
    b = app.named_product_facts_block("Can I take Vitamin D Syntropy if I am on warfarin?",
                                      gated=True)
    assert '"The caution on the product page for <product name> is:"' in b
    assert b.count(WARNING) == 1 and b.count(RULE) == 1


STAY = app._CAUTION_STAY_LINE
CLOSING = app._CAUTION_CLOSING_REMINDER


def test_the_warfarin_block_has_the_stay_line_after_the_caution_and_closes_on_the_reminder():
    # Review round 2, variants a to c: the header rule alone let warfarin snippets through.
    b = app.named_product_facts_block("Can I take Vitamin D Syntropy if I am on warfarin?")
    assert b.count(STAY) == 1 and b.index(STAY) > b.index(WARNING)
    assert b.count(CLOSING) == 1 and b.endswith("\n\n" + CLOSING)
    assert "skip the drug interaction text in the snippets" in CLOSING
    assert "never the label" in CLOSING and "Dr. Glen" not in b


def test_each_opted_in_caution_gets_one_stay_line_and_the_answer_one_reminder():
    b = app.named_product_facts_block("Is Brain Cleanse safe with Sleep Syntropy?")
    assert b.count(STAY) == 2 and b.count(CLOSING) == 1


def test_a_caution_without_the_opt_in_gets_neither():
    b = app.named_product_facts_block("Is Estro-Clear safe?")
    assert "### Estro-Clear" in b and STAY not in b and CLOSING not in b
