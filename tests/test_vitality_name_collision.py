"""Vitality is an everyday word inside two other product names.

Review of the twelve cautions, round 1 (2026-10-25): once Vitality carried a chat caution,
"Is Vitreous Vitality safe while pregnant?" brought Vitality's facts and a "Read the full
caution for Vitality" button, and the bot would have given Vitality's caution as Vitreous
Vitality's. """
import pytest

import app


@pytest.mark.parametrize("q", [
    "Is Vitreous Vitality safe while pregnant?",
    "How much Vitreous Vitality should I take?",
    "Is Vitality Boost Essence in Terrain Restore safe with warfarin?",
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

