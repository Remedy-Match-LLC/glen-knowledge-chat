"""Only an approved description reaches the chatbot (Glen "yes", 2026-10-15).

Any pin used to let a product's whole description into the bot block. Zinc Syntropy pins
only its intro, and its scraped description carries claims Glen never approved.
"""
import copy

import pytest

import app


def _section(q, name):
    b = app.named_product_facts_block(q)
    return b[b.index(f"### {name}"):] if f"### {name}" in b else ""


@pytest.mark.parametrize("slug", ["zinc-syntropy", "clear-lens-eye-drops", "scar-silk",
                                  "microbiome"])
def test_a_description_that_is_not_pinned_stays_out(slug):
    p = app._PRODUCTS["products"][slug]
    assert "description" not in app._pinned_copy(p)
    name = p["name"]
    desc = app._PRICE_SENTENCE.sub("", p.get("description") or "").strip()
    section = _section(f"Tell me about {name}", name)
    for line in section.split("\n")[2:]:
        assert not (desc and line.strip() and line.strip() in desc), line


def test_zinc_syntropys_scraped_claim_stays_out():
    assert "Only 8%" in (app._PRODUCTS["products"]["zinc-syntropy"].get("description") or "")
    assert "Only 8%" not in app.named_product_facts_block("Tell me about Zinc Syntropy")


def test_a_pinned_description_still_reaches_the_bot(monkeypatch):
    # Positive control: the same product with its description pinned shows it.
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["zinc-syntropy"]["copy_pinned"] = ["intro", "description"]
    monkeypatch.setattr(app, "_PRODUCTS", products)
    assert "Only 8%" in app.named_product_facts_block("Tell me about Zinc Syntropy")
