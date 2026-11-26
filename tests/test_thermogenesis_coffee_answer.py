"""The coffee answer for Thermogenesis. Glen, production tab, 25 Nov 2026: "avoid coffee
and other caffeinated drinks". Live the same day, "Can I take Thermogenesis with my
coffee?" opened "Yes" 4 of 4 (production's read-back). Its caution says "Do not combine
with additional caffeine", but coffee was no caution subject, so the caution answer never
fired. The approved caution and the "less than 1 mg of caffeine" answer stay as they are.
"""
import copy

import pytest

import app

OPENING = "Avoid coffee and other caffeinated drinks while taking Thermogenesis."


@pytest.mark.parametrize("q", [
    "Can I take Thermogenesis with my coffee?",
    "Is green tea ok with Thermogenesis?",
    "Can I take Thermogenesis with an energy drink?",
    "Can I drink decaf coffee on Thermogenesis?",
    "Can I have an espresso after my Thermogenesis?",
    "Can I take Thermogenesis with a caffeine pill?",
    # Review round 1.
    "Can I take Thermogenesis with Red Bull?",
    "Can I take Thermogenesis with a Coke?",
    "Can I take Thermogenesis with chai?",
    "Can I take Thermogenesis with a latte?",
    "Can I take Thermogenesis with a soda?",
    "Is it ok to have caffeine while on Thermogenesis?",
])
def test_a_caffeinated_drink_question_opens_with_glens_sentence(q):
    assert app._caution_link_slugs(q) == ["thermogenesis"]
    assert app._caution_openings(q) == [OPENING]


def test_the_sentence_is_glens_wording():
    assert "avoid coffee and other caffeinated drinks" in OPENING.lower()
    assert app._CAUTION_SUBJECT_OPENING["caffeine"].format(name="Thermogenesis") == OPENING


@pytest.mark.parametrize("q", [
    "Does Thermogenesis have caffeine?",
    "Is Thermogenesis caffeine free?",
    "Is Thermogenesis caffeinated?",
    "Are Thermogenesis capsules caffeinated?",
    "How much caffeine is in a capsule of Thermogenesis?",
    # Review round 1: what it is made with, and drinks or words with no caffeine.
    "Does Thermogenesis come with caffeine?",
    "Is Thermogenesis made with caffeine?",
    "Does the green tea in Thermogenesis have caffeine?",
    "Does Thermogenesis contain green tea extract?",
    "Can I take Thermogenesis with herbal tea?",
    "Can I use tea tree oil with Thermogenesis?",
    "Can I take Thermogenesis with baking soda?",
    "Which is better, Thermogenesis or the latter?",
    "How many Thermogenesis capsules can I take a day?",
    "Can I take Thermogenesis with chocolate?",
])
def test_other_questions_are_unchanged(q):
    assert app._caution_openings(q) == []
    assert app._caution_link_slugs(q) == []


def test_before_the_terms_the_reply_opens_with_it_then_quotes_the_caution():
    q = "Can I take Thermogenesis with my coffee?"
    warn = app._PRODUCTS["products"]["thermogenesis"]["warning"].strip()
    reply = app._pre_terms_caution_reply(q)
    assert reply == "\n\n".join([
        OPENING,
        "The caution on the product page for Thermogenesis is:\n\n" + warn,
        app._PRE_TERMS_CAUTION_INVITE])
    assert not reply.lower().startswith("yes")


@pytest.mark.parametrize("member", [False, True])
@pytest.mark.parametrize("long_form", [False, True])
def test_the_model_is_told_to_open_with_it_and_never_say_yes(member, long_form):
    instr = app._caution_synth_instr("Can I take Thermogenesis with my coffee?",
                                     long_form=long_form, member=member)
    assert instr and "word for word: " + OPENING + " For the product it names, never answer yes" in instr
    assert instr.index(OPENING) < instr.index("1. Open")


def test_the_caution_must_name_caffeine(monkeypatch):
    products = copy.deepcopy(app._PRODUCTS)
    rec = products["products"]["thermogenesis"]
    rec["warning"] = rec["warning"].replace("caffeine or other stimulants", "stimulants")
    assert "caffeine" not in rec["warning"].lower()
    monkeypatch.setattr(app, "_PRODUCTS", products)
    q = "Can I take Thermogenesis with my coffee?"
    assert app._caution_openings(q) == []
    assert app._caution_synth_instr(q) is None


def test_a_product_without_a_caffeine_caution_gets_no_opening():
    for slug, pr in app._PRODUCTS["products"].items():
        if slug != "thermogenesis" and pr.get("chat_caution"):
            assert "caffein" not in (pr.get("warning") or "").lower(), slug
    assert app._caution_openings("Can I take Stress Release with coffee?") == []
    assert app._caution_openings("How much Stress Release can I take with my coffee?") == []


def test_no_question_without_a_caution_product_is_affected():
    assert app._caution_openings("Is coffee good for the liver?") == []
    assert app._caution_synth_instr("Is coffee good for the liver?") is None


def test_a_caution_linked_for_another_reason_gets_no_coffee_sentence():
    # Review: a safety question links every caution, so the opening must still check
    # that this product's own caution names caffeine.
    q = "Is Stress Release safe with my coffee?"
    assert app._caution_link_slugs(q) == ["stress-release"]
    assert app._caution_openings(q) == []


def test_a_caffeinated_drink_still_counts():
    q = "Can I take Thermogenesis with a caffeinated drink?"
    assert app._caution_openings(q) == [OPENING]
