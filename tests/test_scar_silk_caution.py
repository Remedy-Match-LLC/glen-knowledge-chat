"""Scar Silk's caution moves from the label to the store page.

Glen approved the caution on 2026-10-09 and the move on 2026-10-15 (production spec
production/05 Formulations/_store-updates/2026-10-15-scar-silk-caution.md). It is not
pinned and has no chat_caution, so it shows on the page only, not in the chatbot.
"""
import copy
import importlib
import json

import pytest

import app

APPROVED = ("Ask your practitioner before use if you take a blood thinner or prescription "
            "medication, or before surgery.")
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))


def test_the_warning_is_the_approved_text_in_both_files():
    assert PRODUCTS["scar-silk"]["warning"] == APPROVED
    assert CORRECTIONS["scar-silk"]["warning"] == APPROVED


def test_nothing_else_about_scar_silk_is_opted_in():
    p = PRODUCTS["scar-silk"]
    assert not p.get("copy_pinned") and not p.get("chat_caution") and not p.get("directions")


def test_a_rebuild_keeps_the_caution_and_the_real_source():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    products["scar-silk"].pop("warning")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products["scar-silk"]["warning"] == APPROVED
    assert products["scar-silk"]["ingredients_source"] == "db-formulations-2026-06-25"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")
    return a


def test_the_page_carries_the_caution_once(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/scar-silk").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["ingredients"]["body"]["warning"] == APPROVED
    assert json.dumps(d).count(APPROVED) == 1


def test_the_chatbot_does_not_carry_it():
    assert "Scar Silk" not in app.named_product_facts_block("Is Scar Silk safe?")
