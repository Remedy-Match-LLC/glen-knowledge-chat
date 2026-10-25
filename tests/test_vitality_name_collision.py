"""Vitality is an everyday word inside two other product names.

Review of the twelve cautions, round 1 (2026-10-25): once Vitality carried a chat caution,
"Is Vitreous Vitality safe while pregnant?" brought Vitality's facts and a "Read the full
caution for Vitality" button, and the bot would have given Vitality's caution as Vitreous
Vitality's. "Vitality matters to me." at the start of a sentence did the same.
"""
import pytest

import app


@pytest.mark.parametrize("q", [
    "Is Vitreous Vitality safe while pregnant?",
    "How much Vitreous Vitality should I take?",
    "Vitality Boost Essence: is it safe with warfarin?",
    "Vitality matters to me. Is NAC safe in pregnancy?",
])
def test_other_wording_brings_no_vitality_facts_or_caution(q):
    assert "### Vitality\n" not in app._named_product_facts(q)
    assert "vitality" not in app._caution_link_slugs(q)


@pytest.mark.parametrize("q", [
    "Can I take Vitality while pregnant?",
    "Is Vitality safe with my blood thinner?",
])
def test_vitality_itself_still_gets_its_caution(q):
    assert "### Vitality\n" in app._named_product_facts(q)
    assert "vitality" in app._caution_link_slugs(q)


@pytest.mark.parametrize("q,slug", [
    ("Liver Support is what I need, which product?", "liver-support"),
    ("Immune Modulation is what my body lacks. What helps?", "immune-modulation"),
])
def test_a_sentence_start_phrase_does_not_name_the_product(q, slug):
    name = app._PRODUCTS["products"][slug]["name"]
    assert f"### {name}\n" not in app._named_product_facts(q)


@pytest.mark.parametrize("q,slug", [
    ("Can I take Liver Support while breastfeeding?", "liver-support"),
    ("Is Immune Modulation safe with my blood thinner?", "immune-modulation"),
])
def test_the_product_named_mid_sentence_still_gets_its_caution(q, slug):
    name = app._PRODUCTS["products"][slug]["name"]
    assert f"### {name}\n" in app._named_product_facts(q)
    assert slug in app._caution_link_slugs(q)


@pytest.mark.parametrize("q", ["Can I take Virex while pregnant?",
                               "can i take virex while pregnant?",
                               "Can I take VIREX while pregnant?"])
def test_virex_matches_in_any_case(q):
    assert "virex" in app._caution_link_slugs(q)
