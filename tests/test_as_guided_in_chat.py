"""Every dose line keeps "or as guided" (Glen, 2026-10-31). Knowledge's read-back of #2338:
a Nerve Pulse answer gave its directions without it."""
import pytest

import app


@pytest.mark.parametrize("q, name", [
    ("How do I take Nerve Pulse?", "Nerve Pulse"),
    ("How do I take Thermogenesis?", "Thermogenesis"),
])
def test_the_rule_follows_a_directions_line_with_or_as_guided(q, name):
    block = app.named_product_facts_block(q)
    assert "or as guided" in block.split("Directions: ", 1)[1].split("\n", 1)[0]
    assert app._AS_GUIDED_RULE.format(name=name) in block


def test_no_rule_without_the_words(monkeypatch):
    # Since 26 Nov every catalogue line says "or as guided", so one is stripped here.
    p = app._PRODUCTS["products"]["vitamin-a-syntropy"]
    assert "directions" in app._pinned_copy(p)
    monkeypatch.setitem(p, "directions", "1 capsule daily with food.")
    block = app.named_product_facts_block("How do I take Vitamin A Syntropy?")
    assert "Directions: 1 capsule daily with food." in block
    assert 'keeping "or as guided"' not in block


def test_no_restated_dose():
    # Production's read-back of #2341: a second dose sentence dropped "or as guided".
    block = app.named_product_facts_block("How do I take Thermogenesis?")
    rule = app._AS_GUIDED_RULE.format(name="Thermogenesis")
    assert rule in block
    assert "Add no other sentence about how many, how often or when to take it" in rule
    assert "do not suggest starting low or working up" in rule
    assert 'never say whether it is within these Directions' in rule
    assert "the user names a medicine, pregnancy, surgery or condition" in rule
    assert "Keep the sentence saying to ask Dr. Glen" in rule
    assert "This rule never shortens a caution." in rule
