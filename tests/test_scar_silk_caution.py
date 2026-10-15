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


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
    return dict(pairs)


def test_neither_file_holds_a_second_scar_silk_entry():
    # Review round 2: json.load keeps only the last of a repeated key.
    for path in ("data/products.json", "data/products-manual-corrections.json"):
        json.load(open(path), object_pairs_hook=_no_duplicate_keys)


def test_no_other_field_already_carries_the_caution():
    # The page could show it twice if the description or intro held it too.
    p = PRODUCTS["scar-silk"]
    for field in ("description", "intro", "panel_note", "directions"):
        assert APPROVED not in (p.get(field) or ""), field


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


@pytest.mark.parametrize("q", ["Is Scar Silk safe?", "What is Scar Silk?",
                               "How much Scar Silk should I take?"])
def test_the_chatbot_does_not_carry_it(q):
    b = app.named_product_facts_block(q)
    assert APPROVED not in b and "Scar Silk" not in b
