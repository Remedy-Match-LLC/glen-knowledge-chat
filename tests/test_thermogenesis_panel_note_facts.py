"""Glen, 2026-11-26: the approved Thermogenesis label note reaches the chat word for word.

A live answer invented "about 6% of the original caffeine level, so it's largely
caffeine-free" because the facts block held no caffeine amount.
"""
import copy

import pytest

import app

NOTE_TAIL = "Contains less than 1 mg of caffeine per capsule."


def _note():
    return app._PRODUCTS["products"]["thermogenesis"]["panel_note"].strip()


def test_the_data_holds_the_approved_line():
    assert _note().endswith(NOTE_TAIL)


@pytest.mark.parametrize("q", [
    "Does Thermogenesis have caffeine?",
    "How much caffeine is in Thermogenesis?",
    "What is in Thermogenesis?",
    "Can I take Thermogenesis with my coffee?",
])
def test_the_note_is_in_the_block_word_for_word(q):
    block = app.named_product_facts_block(q)
    assert f"Label note for Thermogenesis, word for word: {_note()}" in block
    assert app._PANEL_NOTE_CAFFEINE_RULE.format(name="Thermogenesis") in block


def test_the_rule_forbids_the_live_failure():
    rule = app._PANEL_NOTE_CAFFEINE_RULE.format(name="Thermogenesis")
    assert "Never call it caffeine-free" in rule
    assert "never give a percentage" in rule
    assert "never answer no" not in rule
    assert "never say it has no caffeine" in rule
    assert "then give the amount in one sentence after the caution" in rule


def test_a_mixed_question_keeps_the_coffee_opening():
    q = "How much caffeine is in Thermogenesis, and can I take it with coffee?"
    instr = app._caution_synth_instr(q, member=True)
    assert app._CAUTION_SUBJECT_OPENING["caffeine"].format(name="Thermogenesis") in instr
    block = app.named_product_facts_block(q)
    assert app._PANEL_NOTE_CAFFEINE_RULE.format(name="Thermogenesis") in block


def test_no_other_product_gets_a_label_note():
    block = app.named_product_facts_block("What is in Stress Release?")
    assert "Label note for" not in block
    assert app._PANEL_NOTE_FACTS == frozenset({"thermogenesis"})


def test_an_unpinned_ingredients_section_keeps_the_note_out(monkeypatch):
    prods = copy.deepcopy(app._PRODUCTS)
    prods["products"]["thermogenesis"]["copy_pinned"] = ["directions"]
    monkeypatch.setattr(app, "_PRODUCTS", prods)
    block = app.named_product_facts_block("Does Thermogenesis have caffeine?")
    assert "Label note for" not in block


def test_the_coffee_rule_does_not_forbid_the_amount_after_the_caution():
    # Review round 3: "the one line allowed beside the caution" barred the amount.
    assert "allowed beside the caution" not in app._CAUTION_NOT_OURS
    assert "the one line allowed before the caution" in app._CAUTION_NOT_OURS
