"""A product asked about by a former name gets its own directions and caution.

Chat 2690 (2026-11-03) asked "What is in Synergy C and what does it do?". Synergy C is a
former name of Vitamin C Syntropy, listed in its aliases, but the chat matched catalog
names only. The model got no directions and no caution, and answered "1 capsule daily;
can take 2-3 if oxidative stress is high". Each capsule holds vitamin A above the adult
upper limit. Glen "yes", 2026-11-03: a former name no other product shares now counts.
"""
import copy
import json

import pytest

import app

PRODUCTS = json.load(open("data/products.json"))["products"]
VITC = PRODUCTS["vitamin-c-syntropy"]


def test_synergy_c_brings_vitamin_c_syntropy_directions_and_caution():
    block = app.named_product_facts_block("What is in Synergy C and what does it do?")
    assert "### Vitamin C Syntropy" in block
    assert VITC["directions"] in block
    assert VITC["warning"] in block
    assert ('The user wrote "Synergy C", another name for Vitamin C Syntropy. '
            "Answer about Vitamin C Syntropy, by that name, from this text.") in block


@pytest.mark.parametrize("question,slug", [
    ("how much synergy c should i take?", "vitamin-c-syntropy"),   # lowercase + dose cue
    ("How much Zinc Synergy should I take?", "zinc-syntropy"),
    ("how much zinc synergy should i take?", "zinc-syntropy"),
    ("zinc synergy dose?", "zinc-syntropy"),                       # dose word after
    ("Is Magnesium Synergy safe in pregnancy?", "magnesium-syntropy"),
    ("Vitamin D Synergy Powder dose?", "vitamin-d-syntropy"),
    ("Tell me about Seacure Synergy", "seaamino-syntropy"),
    ("Does Sleep Synergy help insomnia?", "sleep-syntropy"),       # round 3: missed before
    ("Is Seacure Synergy vegan?", "seaamino-syntropy"),
    ("Does Zinc Synergy help acne?", "zinc-syntropy"),
    ("Synergy-C dose?", "vitamin-c-syntropy"),                     # hyphen typed
    ("Does Sleep Synergy work? Thanks.", "sleep-syntropy"),        # sentence starts
    ("Hi. Does Zinc Synergy work? Thanks.", "zinc-syntropy"),      # are not Title Case
    ("Does Zinc Synergy Work?", "zinc-syntropy"),                  # one other word only
    ("dose of the zinc synergy?", "zinc-syntropy"),               # "of the" before
    ("zinc synergy supplement?", "zinc-syntropy"),
    ("zinc synergy formula?", "zinc-syntropy"),
    ("Is there zinc synergy with copper? How much zinc synergy should I take?",
     "zinc-syntropy"),                                            # a later occurrence
    ("is synergy c safe while pregnant?", "vitamin-c-syntropy"),  # vinpocetine caution
    ("is magnesium synergy safe in pregnancy?", "magnesium-syntropy"),
    ("How Much Synergy C Should I Take?", "vitamin-c-syntropy"),   # Title Case + dose cue
])
def test_former_names_clients_type(question, slug):
    assert set(app._named_product_spans(question, PRODUCTS)) == {slug}, question


@pytest.mark.parametrize("question", [
    "Is there zinc synergy with copper?",       # an ordinary phrase, no dose cue
    "Is there zinc synergy?",
    "magnesium synergy with vitamin b6",
    "how much vitamin d synergy with k2 do i need?",   # "with": a relation, not a name
    "how much vitamin d synergy with k2 is safe?",
    "how much vitamin a synergy with zinc is safe?",
    "What is vitamin a synergy?",                       # round 3: nutrition questions
    "what is magnesium synergy",
    "Is the vitamin A synergy safe in pregnancy?",
    "I'm pregnant. What is vitamin a synergy?",
    "What Is The Zinc Synergy With Copper?",           # Title Case needs a dose cue
    "How Does Magnesium Synergy Work With Vitamin D?",
    "XSynergy C dose?",                                 # inside a longer word
    "Pro-Synergy C dose?",
    "C+Synergy C dose?",
    "how much zinc synergy and copper do i need?",
    "how much zinc synergy & copper do i need?",
    "what is vitamin a synergy? is it real?",
    "Synergy CX dose?",
])
def test_ordinary_phrases_name_nothing(question):
    assert app._named_product_spans(question, PRODUCTS) == {}, question


def test_an_alias_another_product_shares_is_never_a_former_name():
    taken = {}
    for slug, p in PRODUCTS.items():
        for n in [p.get("name")] + list(p.get("aliases") or []):
            if n:
                taken.setdefault(" ".join(n.lower().split()), set()).add(slug)
    checked = 0
    for slug, p in PRODUCTS.items():
        for a in app._former_names(slug, PRODUCTS):
            assert taken[" ".join(a.lower().split())] == {slug}, (slug, a)
            checked += 1
    assert checked >= 10
    assert "CDS" not in app._former_names("cds-water-purifier--activator", PRODUCTS)


def test_the_catalog_name_gets_no_former_name_line():
    block = app.named_product_facts_block("What does Vitamin C Syntropy do?")
    assert "### Vitamin C Syntropy" in block
    assert "another name for" not in block


def test_a_retired_product_alias_stays_unmatched():
    # Only eligible products are matched, so an inactive product's alias names nothing.
    products = copy.deepcopy(PRODUCTS)
    products["vitamin-c-syntropy"]["inactive"] = True
    assert "Synergy C" in app._former_names("vitamin-c-syntropy", products)
    assert app._named_product_spans("How much Synergy C should I take?", products) == {}


def test_an_alias_added_to_a_second_product_stops_matching_at_once():
    # No stale table: the same catalog object, edited, is read afresh (review round 2).
    products = copy.deepcopy(PRODUCTS)
    q = "How much Synergy C should I take?"
    assert set(app._named_product_spans(q, products)) == {"vitamin-c-syntropy"}
    products["zinc-syntropy"]["aliases"] = list(products["zinc-syntropy"]["aliases"]) + [
        "Synergy C"]
    assert app._named_product_spans(q, products) == {}


def test_a_shared_alias_never_matches():
    # Two eligible products share an alias: neither is named by it.
    products = copy.deepcopy(PRODUCTS)
    products["zinc-syntropy"]["aliases"] = ["Synergy Z"]
    products["magnesium-syntropy"]["aliases"] = ["Synergy Z"]
    assert app._named_product_spans("How much Synergy Z should I take?", products) == {}
    products["magnesium-syntropy"]["aliases"] = []
    assert set(app._named_product_spans("How much Synergy Z should I take?",
                                        products)) == {"zinc-syntropy"}


def test_a_false_former_name_gives_no_caution_reply():
    # The pre-Terms gate swaps the answer for a caution. An ordinary vitamin question
    # must not trigger it through a former name (review round 1).
    for q in ("how much vitamin d synergy with k2 is safe?",
              "how much vitamin a synergy with zinc is safe?",
              "Is the vitamin A synergy safe in pregnancy?",
              "I'm pregnant. What is vitamin a synergy?"):
        assert not app._pre_terms_caution_reply(q, []), q


def test_the_former_name_line_survives_a_trademark_sign_earlier():
    block = app.named_product_facts_block("I use Zinc Syntropy\u2122 daily. What is Synergy C?")
    assert 'The user wrote "Synergy C", another name for Vitamin C Syntropy.' in block


def test_title_case_with_a_dose_word_still_matches():
    assert set(app._named_product_spans("How Much Synergy C Should I Take?",
                                        PRODUCTS)) == {"vitamin-c-syntropy"}


def test_a_short_form_or_single_word_alias_is_never_a_former_name():
    # "Sleep", "Energy" and "Fat Metabolism" are everyday words (review round 3).
    products = copy.deepcopy(PRODUCTS)
    products["zinc-syntropy"]["aliases"] = ["Zinc", "Zinc Syntropy", "Syntropy", "Zinc Plus"]
    assert app._former_names("zinc-syntropy", products) == ["Zinc Plus"]
    assert "Sleep" not in app._former_names("sleep", products)
    assert "Fat Metabolism" not in app._former_names("fat-metabolism", products)


def test_a_guarded_product_gets_no_former_name_match(monkeypatch):
    q = "How much Synergy C should I take?"
    assert set(app._named_product_spans(q, PRODUCTS)) == {"vitamin-c-syntropy"}
    monkeypatch.setattr(app, "_YIELDS_TO_LONGER_NAMES",
                        set(app._YIELDS_TO_LONGER_NAMES) | {"vitamin-c-syntropy"})
    assert app._named_product_spans(q, PRODUCTS) == {}


def test_the_longest_former_name_is_quoted():
    block = app.named_product_facts_block("Vitamin D Synergy Powder dose?")
    assert 'The user wrote "Vitamin D Synergy Powder", another name for' in block


def test_a_long_question_stays_fast():
    import time
    q = "Zinc Synergy And " * 1500
    t = time.perf_counter()
    app._named_product_spans(q, PRODUCTS)
    assert time.perf_counter() - t < 1.5


def test_a_hyphen_spelling_on_a_second_product_counts_as_shared():
    products = copy.deepcopy(PRODUCTS)
    products["zinc-syntropy"]["aliases"] = ["Synergy-C"]
    assert app._named_product_spans("How much Synergy C should I take?", products) == {}
