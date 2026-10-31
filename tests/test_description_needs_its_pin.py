"""Only an approved description reaches the chatbot (Glen "yes", 2026-10-15).

Any pin used to let a product's whole description into the bot block. Zinc Syntropy pins
only its intro, and its scraped description carries claims Glen never approved.
"""
import copy

import pytest

import app


MARKER = "Unapproved marker sentence for the description test."


# Scar Silk's description is pinned since 2026-10-30 (Glen "approve"); Iron Out takes its
# place as the product whose directions alone are pinned.
@pytest.mark.parametrize("slug", ["zinc-syntropy", "clear-lens-eye-drops", "iron-out",
                                  "microbiome"])
def test_a_description_that_is_not_pinned_stays_out(monkeypatch, slug):
    # Review round 2: a marker, so a truncated leak or a missing block cannot pass quietly.
    products = copy.deepcopy(app._PRODUCTS)
    p = products["products"][slug]
    assert "description" not in app._pinned_copy(p)
    p["description"] = MARKER
    monkeypatch.setattr(app, "_PRODUCTS", products)
    b = app.named_product_facts_block(f"How do I take {p['name']}?")
    assert MARKER not in b
    # Control: pinning the description lets the same marker through.
    p["copy_pinned"] = sorted(app._pinned_copy(p) | {"description"})
    assert MARKER in app.named_product_facts_block(f"How do I take {p['name']}?")


def test_zinc_syntropys_scraped_claim_stays_out():
    assert "Only 8%" in (app._PRODUCTS["products"]["zinc-syntropy"].get("description") or "")
    assert "Only 8%" not in app.named_product_facts_block("Tell me about Zinc Syntropy")


def test_a_pinned_description_still_reaches_the_bot(monkeypatch):
    # Positive control: the same product with its description pinned shows it.
    products = copy.deepcopy(app._PRODUCTS)
    products["products"]["zinc-syntropy"]["copy_pinned"] = ["intro", "description"]
    monkeypatch.setattr(app, "_PRODUCTS", products)
    assert "Only 8%" in app.named_product_facts_block("Tell me about Zinc Syntropy")
