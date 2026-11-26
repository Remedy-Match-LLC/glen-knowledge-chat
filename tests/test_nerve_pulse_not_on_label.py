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
            "Before everything below, make this the answer's first sentence, word for word: "
            + line)
        assert "Never say the product contains it" in instr


def test_the_pre_terms_and_member_answers_carry_it():
    q = "Does Nerve Pulse contain vinpocetine? I am pregnant."
    reply = app._pre_terms_caution_reply(q)
    assert VINPO in reply and reply.index(VINPO) < reply.index(app._PRE_TERMS_CAUTION_INVITE)
    assert "Before step 1, state, word for word: " + VINPO in app._caution_synth_instr(
        q, member=True)


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
])
def test_no_line_elsewhere(q):
    assert app._not_on_label_lines(q) == []


def test_the_listed_ingredients_are_truly_absent_from_the_label():
    p = app._PRODUCTS["products"]["nerve-pulse"]
    import re
    text = " ".join(i["name"] for i in p["ingredients"]) + " " + p["description"]
    for _label, pat in app._NOT_ON_LABEL["nerve-pulse"]:
        assert not re.search(pat, text, re.IGNORECASE), _label
