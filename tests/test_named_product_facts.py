"""A product the user names reaches the chat with its approved dose and caution.

The chat does not search `specific-formulations`. Asked "How much Angiostasis should I
take?" on 2026-10-08, the model had only a link row, took AngiogenX's dose from wet-AMD
snippets and told the client they meant AngiogenX. named_product_facts_block carries the
product's own store text into the prompt.

Review round 1 (2026-10-09) found plain-substring matching brought products into ordinary
questions ("relax", "uncomfortable", "heavy metals"), and aliases mapped old names onto
other products. Only a pinned product's own name, as whole words, now counts.
"""
import inspect
import json

import pytest

import app

ANGIO = json.load(open("data/products.json"))["products"]["angiostasis"]


def test_a_named_product_brings_its_directions_and_full_caution():
    block = app.named_product_facts_block("How much Angiostasis should I take each day?")
    assert "### Angiostasis" in block
    assert ANGIO["directions"] in block
    assert ANGIO["warning"] in block
    assert "never tell them they meant a different product" in block


def test_a_multi_word_name_matches_in_catalog_casing_only():
    assert "### Clear the Way" in app.named_product_facts_block("What dose of Clear the Way?")
    assert app.named_product_facts_block("Can I clear the way for my lymph?") == ""
    assert app.named_product_facts_block("Clear the way for my lymph?") == ""


@pytest.mark.parametrize("question", [
    "How do I support healthy circulation?",
    "",
    "How can I relax before bed?",
    "I feel uncomfortable after meals",
    "I want to transform my health",
    "more energy and vitality please",
    "How do I detox heavy metals?",
    "how do I moisturize dry skin?",        # Moisturize is a pinned one-word name
    "what is angiostasis?",                 # a one-word name must be capitalised
    "What is AngiostasisXYZ123?",           # the whole name must stand as a word
    "Tell me about Dental Regen Powder",    # an old name: aliases never count
    "reverse age naturally",                # review round 3
    "I have acetaldehyde detox issues after drinking",
    "Moisturize my skin. What helps?",      # an everyday word never qualifies
    "Is Electrolyte Mineral Manna good?",   # never recommended (Glen)
    "Dose of Aller-Free HomeoEnergetic Drops?",
])
def test_ordinary_or_unknown_wording_brings_no_block(question):
    assert app.named_product_facts_block(question) == "", question


def test_only_the_named_product_is_carried():
    block = app.named_product_facts_block("What is Angiostasis?")
    assert block.count("### ") == 1
    assert "### AngiogenX" not in block


def test_an_unpinned_product_brings_no_facts():
    products = app._PRODUCTS["products"]
    slug, p = next((s, p) for s, p in products.items()
                   if not p.get("copy_pinned") and not p.get("inactive")
                   and len((p.get("name") or "").split()) >= 2 and p.get("description"))
    assert app.named_product_facts_block(f"What is {p['name']}?") == "", slug


def test_an_inactive_pinned_product_brings_no_facts(monkeypatch):
    monkeypatch.setitem(app._PRODUCTS["products"]["angiostasis"], "inactive", True)
    assert app.named_product_facts_block("What is Angiostasis?") == ""


def test_the_longest_name_wins():
    products = {"a": {"name": "Stamina Plus", "copy_pinned": ["intro"], "description": "A."},
                "b": {"name": "Stamina Plus: Full B", "copy_pinned": ["intro"], "description": "B."}}
    assert set(app._named_product_spans("Is Stamina Plus: Full B good?", products)) == {"b"}
    assert set(app._named_product_spans("Is Stamina Plus good?", products)) == {"a"}


def test_a_price_line_is_stripped(monkeypatch):
    monkeypatch.setitem(app._PRODUCTS["products"]["angiostasis"], "description",
                        "Angiostasis . Price: $70. Each vegicap supplies green tea.")
    block = app.named_product_facts_block("What is Angiostasis?")
    assert "Price:" not in block and "$70" not in block
    assert "Each vegicap supplies green tea." in block


def test_the_block_is_capped_at_three_products():
    block = app.named_product_facts_block(
        "Compare Angiostasis, AngiogenX, Clear the Way, Appestat and Iron Syntropy")
    assert block.count("### ") == app._NAMED_FACTS_MAX_PRODUCTS


def test_every_answer_path_appends_the_block_after_the_instruction():
    for fn in (app.chat, app._generate_full_answer, app._full_report_stream):
        src = inspect.getsource(fn)
        assert src.index("{synth_instr}") < src.index("named_product_facts_block(query)"), fn.__name__


def test_a_gated_turn_keeps_the_label_text_as_label_information():
    # Glen, 2026-10-09: a non-member who names a product gets its label dose and caution.
    q = "How much Angiostasis should I take, and are there any cautions?"
    open_, gated = app.named_product_facts_block(q), app.named_product_facts_block(q, gated=True)
    assert app._GATED_FACTS_NOTE not in open_
    assert app._GATED_FACTS_NOTE in gated
    assert ANGIO["directions"] in gated and ANGIO["warning"] in gated
    src = inspect.getsource(app.chat)
    gate = src.index("_system = _system + _EDUCATE_ONLY_POLICY")
    after = src[gate:gate + 700]
    assert "named_product_facts_block(query, gated=True)" in after


def test_the_sources_line_and_the_cta_point_at_this_product():
    block = app.named_product_facts_block("What is Angiostasis?")
    assert "The Sources line names this product text only" in block
    assert "a page CTA links this product's Page" in block
    assert "Page: " in block and block.split("Page: ")[1].split("\n")[0].endswith(
        "/begin/product/angiostasis")


def test_a_product_with_no_text_brings_no_block():
    # Seven pinned Mithreal apparel items have no description, directions or caution.
    products = app._PRODUCTS["products"]
    slug = "mithreal-silver-socks"
    p = products[slug]
    assert p.get("copy_pinned") and not (p.get("description") or p.get("directions")
                                          or p.get("warning")), slug
    assert set(app._named_product_spans(f"Tell me about {p['name']}", products)) == {slug}
    assert app.named_product_facts_block(f"Tell me about {p['name']}") == ""


class _FakeStream:
    def __init__(self, sink, **kw):
        sink.append(kw)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter(["ok"])


@pytest.mark.parametrize("gated", [True, False])
def test_the_chat_route_sends_the_gated_variant_on_a_gated_turn(monkeypatch, gated):
    sent = []
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: ["one match"])
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet about AngiogenX", []))
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: gated)
    monkeypatch.setattr(app._cl.messages, "stream", lambda **kw: _FakeStream(sent, **kw))
    q = "How much Angiostasis should I take, and are there any cautions?"
    r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                  "mode": "brief"}, buffered=True)
    assert sent, r.get_data(as_text=True)[:500]
    last = sent[-1]["messages"][-1]["content"]
    assert ANGIO["warning"] in last
    assert (app._GATED_FACTS_NOTE in last) is gated
    assert (app._EDUCATE_ONLY_POLICY in sent[-1]["system"]) is gated
