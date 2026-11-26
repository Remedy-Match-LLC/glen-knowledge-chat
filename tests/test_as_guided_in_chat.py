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


def test_no_rule_without_the_words():
    products = app._PRODUCTS["products"]
    checked = 0
    for slug, p in products.items():
        d = (p.get("directions") or "").strip()
        if not ("directions" in app._pinned_copy(p) and d and p.get("name")
                and "or as guided" not in d):
            continue
        block = app.named_product_facts_block(f"How do I take {p['name'].strip()}?")
        if f"Directions: {d}" not in block:
            continue        # its directions sit in the description, or the name needs a cue
        assert 'keeping "or as guided"' not in block, slug
        checked += 1
    assert checked >= 1   # review round 2: the absence must be exercised on a real line


def test_no_restated_dose():
    # Production's read-back of #2341: a second dose sentence dropped "or as guided".
    block = app.named_product_facts_block("How do I take Thermogenesis?")
    rule = app._AS_GUIDED_RULE.format(name="Thermogenesis")
    assert rule in block
    assert "Add no other sentence about how many, how often or when to take it" in rule
    assert "do not suggest starting low or working up" in rule
    assert 'say "That is within the Directions." in those words, without repeating the amount' in rule
    assert "if the user names a medicine, pregnancy, surgery or condition" in rule
    assert "keep the sentence saying to ask Dr. Glen" in rule
    assert "This rule never shortens a caution." in rule
