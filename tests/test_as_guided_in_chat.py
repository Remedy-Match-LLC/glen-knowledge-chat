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
    slug = next(s for s, p in products.items()
                if "directions" in app._pinned_copy(p) and p.get("directions")
                and "or as guided" not in p["directions"] and p.get("name"))
    block = app.named_product_facts_block(f"How do I take {products[slug]['name'].strip()}?")
    assert 'keeping "or as guided"' not in block
