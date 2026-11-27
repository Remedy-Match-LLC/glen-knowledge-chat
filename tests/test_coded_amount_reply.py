"""Knowledge's read-back of #2354 (2026-11-27): the model still said "within" for 5 Nerve
Repair a day in 8 of 18 answers. Glen, platform tab, "yes": code writes the reply to a
plain "how many can I take" question in the approved lines."""
import pytest

import app

NR_DIRS = "1 capsule up to 5 times daily with food, or as guided."


def _reply(q, prior=None):
    return app._coded_reply(q, prior or [])


def test_the_approved_words_are_pinned():
    assert app._CODED_AMOUNT_OPEN == "The label directions for {name} are: {directions}"
    assert app._CODED_AMOUNT_OVER == "That is more than these directions."
    assert app._CODED_AMOUNT_ASK == "For anything beyond these directions, please ask Dr. Glen."


def test_five_a_day_gets_the_directions_and_never_within():
    r, cta = _reply("Can I take 5 Nerve Repair a day?")
    assert r.startswith("The label directions for Nerve Repair are: " + NR_DIRS)
    assert "within" not in r.lower()
    assert app._CODED_AMOUNT_OVER not in r
    assert app._CODED_AMOUNT_ASK in r
    assert cta and cta["target"].endswith("/begin/product/nerve-repair")


def test_ten_a_day_is_more_than_the_directions():
    r, _ = _reply("Can I take 10 Nerve Repair a day?")
    assert app._CODED_AMOUNT_OVER in r


def test_the_caution_is_quoted_word_for_word():
    r, _ = _reply("Can I take 5 Nerve Repair a day?")
    warn = app._PRODUCTS["products"]["nerve-repair"]["warning"].strip()
    assert warn in r


def test_how_many_with_no_number_gets_the_reply():
    r, _ = _reply("How many Nerve Repair can I take?")
    assert r.startswith("The label directions for Nerve Repair are:")
    assert app._CODED_AMOUNT_OVER not in r


@pytest.mark.parametrize("q", [
    "I'm pregnant, can I take 2 Nerve Repair a day?",
    "Can I take 5 Nerve Repair a day with my blood pressure medicine?",
    "Can my daughter take 2 Nerve Repair a day?",
    "How long does a 60 capsule bottle of Nerve Repair last?",
    "Can I take 5 Nerve Repair and 2 Moisturize a day?",
    "How much does Nerve Repair cost?",
    "Tell me about Nerve Repair",
    "How many capsules are in a bottle of Nerve Repair?",
    "How many reviews does Nerve Repair have?",
    "What's the maximum benefit of Nerve Repair?",
    "My friend Max told me about Nerve Repair, what is it?",
    "How many Nerve Repair should my grandson take?",
    "I'm 70, how many Nerve Repair can I take?",
    "I take Xanax, how many Nerve Repair can I take?",
    "I feel dizzy after Nerve Repair, how many can I take?",
    "I've been taking 4 Nerve Repair a day, is that ok?",
    "I have stage 4 cancer. Can I take 2 Nerve Repair a day?",
])
def test_anything_else_goes_to_the_model(q):
    assert app._coded_amount_reply(q, [])[0] == ""
    # Review round 2: through the dispatcher production calls, too.
    assert not app._coded_reply(q, [])[0].startswith("The label directions for Nerve Repair")


def test_a_product_without_pinned_directions_goes_to_the_model(monkeypatch):
    p = app._PRODUCTS["products"]["nerve-repair"]
    monkeypatch.setitem(p, "copy_pinned", [c for c in p.get("copy_pinned", []) if c != "directions"])
    assert app._coded_amount_reply("Can I take 5 Nerve Repair a day?", [])[0] == ""


def test_a_caution_that_cannot_be_quoted_goes_to_the_model(monkeypatch):
    monkeypatch.setattr(app, "_verbatim_caution_paras", lambda *a, **k: [])
    assert app._coded_amount_reply("Can I take 5 Nerve Repair a day?", [])[0] == ""


def test_allerfree_keeps_its_own_reply():
    assert app._coded_amount_reply("How many AllerFree drops can I take?", [])[0] == ""


def test_allerfree_is_refused_even_if_the_matcher_finds_it(monkeypatch):
    slug = app._NAMED_DIRECTIONS_ONLY[0]
    monkeypatch.setattr(app, "_named_product_spans", lambda *a, **k: {slug: (0, 1)})
    monkeypatch.setattr(app, "_pinned_copy", lambda pr: {"directions", "description"})
    assert app._coded_amount_reply("How many AllerFree drops can I take?", [])[0] == ""


@pytest.mark.parametrize("q", [
    "Can I take 5 Nerve Repair a day?",
    "can i take five nerve repair capsules daily?",
    "How many Nerve Repair can I take?",
    "How many Nerve Repair capsules should I take a day?",
    "Is it ok to take 2 Nerve Repair a day?",
    "What is the maximum dose of Nerve Repair?",
    "Hi, can I take 3 Nerve Repair at once?",
])
def test_the_plain_forms_are_recognised(q):
    assert app._plain_amount_question(q, "Nerve Repair")


def test_a_label_with_or_with_is_not_read():
    assert app._directions_most("Take 1 capsule daily with food, or with cravings for "
                                "sugar or stimulants, or as guided.") is None


def test_a_situation_in_an_earlier_turn_goes_to_the_model():
    assert app._coded_reply("How many Nerve Repair can I take?", ["I am on lithium"])[0] == ""
    assert app._coded_reply("How many Nerve Repair can I take?",
                            ["What is Nerve Repair for?"])[0].startswith("The label directions")
