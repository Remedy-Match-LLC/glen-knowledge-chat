"""Production's read-back after #2336: "Does Nerve Pulse contain vinpocetine?" got "Yes,
Nerve Pulse contains vinpocetine, 1 mg per capsule" 4 of 4. Code now writes the opening."""
import pytest

import app

VINPO = "The current Nerve Pulse label does not list vinpocetine."


@pytest.mark.parametrize("q, line", [
    ("Does Nerve Pulse contain vinpocetine?", VINPO),
    ("Is there vinpocetine in Nerve Pulse?", VINPO),
    ("Is there Lion's Mane in Nerve Pulse?", "The current Nerve Pulse label does not list Lion's Mane."),
    ("Does Nerve Pulse have ginkgo?", "The current Nerve Pulse label does not list ginkgo."),
    ("Does Nerve Pulse contain ALCAR?",
     "The current Nerve Pulse label does not list acetyl-L-carnitine (ALCAR)."),
])
def test_a_contents_question_opens_with_the_line(q, line):
    assert app._not_on_label_lines(q) == [line]
    for mode in ("brief", "full"):
        instr = app._chat_synth_instr(q, None, mode, False)
        assert instr.startswith(
            "Before everything below, make this the answer's first sentence, word for word, "
            f'without the quotation marks: "{line}" It does not count toward any word limit')
        assert "Never say Nerve Pulse contains " in instr
        assert app._SEPARATE_PRODUCT_NOTE in instr


def test_the_pre_terms_and_member_answers_carry_it():
    q = "Does Nerve Pulse contain vinpocetine? I am pregnant."
    reply = app._pre_terms_caution_reply(q)
    vcaution = app._PRODUCTS["products"]["vinpocetine"]["warning"].strip()
    assert VINPO in reply and reply.index(VINPO) < reply.index(vcaution)
    member = app._caution_synth_instr(q, member=True)
    assert f'Before step 1, state, word for word, without the quotation marks: "{VINPO}"' in member
    assert app._SEPARATE_PRODUCT_NOTE in member


@pytest.mark.parametrize("q", [
    "Does Nerve Repair contain vinpocetine?",
    "Is vinpocetine safe? Also, what is in Nerve Pulse?",
    "Can I take Nerve Pulse with ginkgo?",
    "Does Nerve Pulse contain huperzine?",
    "What is in Nerve Pulse?",
    # Each isolates one guard (mutation check).
    "Does Nerve Pulse have ginkgo, and can I take it with ginkgo?",   # combination
    "Nerve Pulse vinpocetine?",                                     # no contents word
    "Is vinpocetine safe? Does Nerve Pulse have huperzine?",          # another clause
    "Does Nerve Pulse have side effects like ginkgo does?",           # effects, not contents
    "Does Nerve Pulse contain huperzine, and does Nerve Repair contain vinpocetine?",
])

def test_no_line_elsewhere(q):
    assert app._not_on_label_lines(q) == []


def test_the_listed_ingredients_are_truly_absent_from_the_label():
    p = app._PRODUCTS["products"]["nerve-pulse"]
    import re
    text = " ".join(i["name"] for i in p["ingredients"]) + " " + p["description"]
    for _label, pat in app._NOT_ON_LABEL["nerve-pulse"]:
        assert not re.search(pat, text, re.IGNORECASE), _label


@pytest.mark.parametrize("q", [
    "Is Nerve Pulse made with vinpocetine?",
    "Does Nerve Pulse contain vinpocetine? I take prenatal vitamins.",
])
def test_these_contents_questions_still_get_it(q):
    # Review round 2: the combination veto reads only the ingredient's own sentence.
    assert app._not_on_label_lines(q) == [VINPO]


def test_both_openings_merge_into_one_instruction():
    q = "Does Nerve Pulse contain vinpocetine, and does Thermogenesis have caffeine?"
    instr = app._chat_synth_instr(q, None, "brief", False)
    assert instr.count("Before everything below") == 1
    assert VINPO in instr and "The Thermogenesis label says:" in instr
    assert "Never say Nerve Pulse contains vinpocetine" in instr


def test_an_ingredient_goes_with_the_nearest_product():
    # Review round 3.
    q = "Does Thermogenesis contain vinpocetine, and does Nerve Pulse have ginkgo?"
    assert app._not_on_label_lines(q) == ["The current Nerve Pulse label does not list ginkgo."]


def test_a_combination_anywhere_in_the_sentence_blocks_it():
    # Safety over reach: a "with" in the same sentence may be about this product.
    q = "Does Nerve Pulse contain vinpocetine, and can I drink coffee with Thermogenesis?"
    assert app._not_on_label_lines(q) == []
