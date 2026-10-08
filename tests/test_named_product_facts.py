"""A product the user names reaches the chat with its approved dose and caution.

The chat does not search `specific-formulations`. Asked "How much Angiostasis should I
take?" on 2026-10-08, the model had only a link row, took AngiogenX's dose from wet-AMD
snippets and told the client they meant AngiogenX. named_product_facts_block carries the
product's own store text into the prompt.
"""
import inspect
import json

import app

ANGIO = json.load(open("data/products.json"))["products"]["angiostasis"]


def test_a_named_product_brings_its_directions_and_full_caution():
    block = app.named_product_facts_block("How much Angiostasis should I take each day?")
    assert "### Angiostasis" in block
    assert ANGIO["directions"] in block
    assert ANGIO["warning"] in block
    assert "never tell them they meant a different product" in block


def test_no_named_product_means_no_block():
    assert app.named_product_facts_block("How do I support healthy circulation?") == ""
    assert app.named_product_facts_block("") == ""


def test_only_the_named_product_is_carried():
    block = app.named_product_facts_block("What is Angiostasis?")
    assert block.count("### ") == 1
    assert "### AngiogenX" not in block


def test_a_retired_product_brings_no_facts(monkeypatch):
    products = app._PRODUCTS["products"]
    slug, retired = next((s, p) for s, p in products.items()
                         if p.get("inactive") and not p.get("superseded_by")
                         and len(p.get("name") or "") >= 12)
    # Give it text, so an empty block can only come from the inactive check.
    monkeypatch.setitem(retired, "directions", "Take 9 capsules.")
    assert app.named_product_facts_block(f"What is {retired['name']}?") == "", slug
    monkeypatch.delitem(retired, "inactive")
    assert "Take 9 capsules." in app.named_product_facts_block(f"What is {retired['name']}?"), slug


def test_the_block_is_capped_at_three_products():
    block = app.named_product_facts_block(
        "Compare Angiostasis, AngiogenX, Brain Cleanse, Terrain Restore and Clear the Way")
    assert 1 <= block.count("### ") <= app._NAMED_FACTS_MAX_PRODUCTS


def test_every_answer_path_appends_the_block_after_the_instruction():
    for fn in (app.chat, app._generate_full_answer, app._full_report_stream):
        src = inspect.getsource(fn)
        assert src.count("named_product_facts_block(query)") == 1, fn.__name__
        assert src.index("{synth_instr}") < src.index("named_product_facts_block(query)"), fn.__name__


def test_the_facts_builder_refuses_an_inactive_slug_itself(monkeypatch):
    # Both resolvers already drop retired products. This holds if one stops doing so.
    products = app._PRODUCTS["products"]
    slug = next(s for s, p in products.items() if p.get("inactive"))
    monkeypatch.setitem(products[slug], "directions", "Take 9 capsules.")
    monkeypatch.setattr(app, "_catalog_link_matches",
                        lambda text, aliases: {"X": f"https://x/begin/product/{slug}"})
    assert app.named_product_facts_block("anything at all") == "", slug
