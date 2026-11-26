"""The label's caffeine sentence is written by code (production read-back after #2335).

"Is Thermogenesis caffeine-free?" opened "Yes, Thermogenesis is nearly caffeine-free" in 3
of 6 live answers, and the pre-Terms reply to an amount-and-coffee question never gave the
amount. Glen approved the line on 2026-11-26 ("yes" to adding the caffeine note to the chat).
"""
import pytest

import app

LINE = 'The Thermogenesis label says: "Contains less than 1 mg of caffeine per capsule."'
MIXED = "How much caffeine is in Thermogenesis, and can I take it with coffee?"


@pytest.mark.parametrize("q", [
    "Does Thermogenesis have caffeine?",
    "Is Thermogenesis caffeine-free?",
    "Does Thermogenesis contain caffeine?",
    "How much caffeine is in Thermogenesis?",
])
def test_a_caffeine_question_opens_with_the_label_line(q):
    assert app._label_caffeine_lines(q) == [LINE]
    for mode in ("brief", "full"):
        instr = app._chat_synth_instr(q, None, mode, False)
        assert instr.startswith(
            "Before everything below, make this the answer's first sentence, word for word: "
            + LINE)
        assert "nearly caffeine-free" in instr
        assert "any other figure for its caffeine" in instr


def test_the_pre_terms_reply_gives_the_amount_after_the_caution():
    reply = app._pre_terms_caution_reply(MIXED)
    opening = app._CAUTION_SUBJECT_OPENING["caffeine"].format(name="Thermogenesis")
    warning = app._PRODUCTS["products"]["thermogenesis"]["warning"].strip()
    assert reply.startswith(opening)
    assert reply.index(warning) < reply.index(LINE) < reply.index(app._PRE_TERMS_CAUTION_INVITE)


def test_a_member_mixed_answer_adds_the_line_after_the_caution():
    instr = app._caution_synth_instr(MIXED, member=True)
    assert ("Right after the caution, add this sentence, word for word, as the only sentence "
            "about the caffeine amount: " + LINE) in instr


@pytest.mark.parametrize("q", [
    "What is in Thermogenesis?",
    "Does Stress Release have caffeine?",
    "Is coffee bad for me?",
])
def test_no_line_without_caffeine_and_a_label_note_product(q):
    assert app._label_caffeine_lines(q) == []
    assert not app._chat_synth_instr(q, None, "brief", False).startswith("Before everything")


def test_the_line_needs_the_pinned_ingredients(monkeypatch):
    import copy
    prods = copy.deepcopy(app._PRODUCTS)
    prods["products"]["thermogenesis"]["copy_pinned"] = ["directions"]
    monkeypatch.setattr(app, "_PRODUCTS", prods)
    assert app._label_caffeine_lines("Does Thermogenesis have caffeine?") == []


@pytest.mark.parametrize("q", [
    "I'm sensitive to caffeine, is Thermogenesis ok?",
    "Does Thermogenesis work better than caffeine?",
    "Can I drink decaf with Thermogenesis?",
    "I have a heart condition and avoid caffeine. Can I take Thermogenesis?",
    "How much caffeine is in Thermogenesis? I'm sensitive to it.",
    "Does Thermogenesis have caffeine, and is it safe for my heart?",
])
def test_a_use_or_comparison_question_gets_no_opening(q):
    # Review round 1: "less than 1 mg" first would read as reassurance.
    assert app._label_caffeine_instr(q) == ""


def test_caffeine_about_another_product_brings_no_line():
    # Review round 2.
    q = "Does Stress Release contain caffeine? Also, what is in Thermogenesis?"
    assert app._label_caffeine_lines(q) == []


@pytest.mark.parametrize("q", [
    "What's the caffeine content of Thermogenesis?",
    "Does Thermogenesis have caffeine or green tea?",
    "Does Thermogenesis have caffeine? Ok thanks",
])
def test_these_contents_questions_get_the_opening(q):
    # Review round 3.
    assert app._label_caffeine_instr(q).startswith("Before everything below")


@pytest.mark.parametrize("q", ["Is Thermogenesis caffeine-free?", "Does Thermogenesis have caffeine?"])
def test_the_answer_is_tightened(q):
    # Glen "yes, tighten the caffeine answer" 2026-11-26.
    instr = app._chat_synth_instr(q, None, "brief", False)
    assert app._CAFFEINE_ANSWER_TIGHT in instr
    assert "Do not answer yes or no before or after it" in instr
    assert "negligible, trace or insignificant" in instr
    assert "Do not suggest or recommend any other product as caffeine-free" in instr
    assert app._CAFFEINE_ANSWER_TIGHT in app.named_product_facts_block(q)
