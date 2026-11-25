"""Bone Builder and Dental Powder carry Glen's vitamin K caution (production spec
2026-10-31-vitamin-k-blood-thinner-caution; Glen "yes" 31 Oct, Dental Powder in place of
the retired Dental Regen Powder record).

Live chat, 2026-11-25: "Can I take Bone Builder if I am on warfarin?" got no caution and
advice to let a doctor adjust the warfarin dose. `chat_caution` gives the bot the real line.
The sentence is word for word Vitamin D Syntropy's.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WARNING = ("If you take a blood thinner such as warfarin, consult your physician before use, "
           "because vitamin K affects it.")
LINE = f"Caution: {WARNING}"
PRODUCTS = [("bone-builder", "Bone Builder"), ("dental-powder", "Dental Powder")]


@pytest.fixture(scope="module")
def catalogue():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    return a


@pytest.mark.parametrize("slug,name", PRODUCTS)
def test_the_record_and_its_pin_carry_the_approved_caution(catalogue, corrections, slug, name):
    assert catalogue[slug]["name"] == name
    assert catalogue[slug]["warning"] == WARNING
    assert catalogue[slug]["chat_caution"] is True
    assert corrections[slug]["warning"] == WARNING
    assert corrections[slug]["chat_caution"] is True


def test_the_sentence_is_vitamin_d_syntropys(catalogue):
    assert catalogue["vitamin-d-syntropy"]["warning"].startswith(WARNING)


def test_the_replay_restores_the_caution(catalogue, corrections):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import apply_enrichment as ae
    products = json.loads(json.dumps(catalogue))
    for slug, _ in PRODUCTS:
        products[slug].pop("warning")
        products[slug].pop("chat_caution")
    ae.apply_corrections(products, corrections)
    for slug, _ in PRODUCTS:
        assert products[slug]["warning"] == WARNING
        assert products[slug]["chat_caution"] is True
        # The replay keeps each record's ingredient source tag (review round 1).
        assert products[slug]["ingredients_source"] == catalogue[slug]["ingredients_source"]
    # Bone Builder's D3 dose fix is still applied alongside.
    d3 = [i for i in products["bone-builder"]["ingredients"]
          if "cholecalciferol" in str(i.get("name", "")).lower()]
    assert d3 and d3[0]["dose"] == "6.25 mcg (250 IU)"


@pytest.mark.parametrize("q,name", [
    ("What is Bone Builder?", "Bone Builder"),
    ("How do I take Dental Powder?", "Dental Powder"),
])
def test_the_block_quotes_the_caution_once(appmod, q, name):
    block = appmod.named_product_facts_block(q)
    assert f"### {name}" in block
    assert block.count(LINE) == 1
    assert "has no caution on its product page" not in block


@pytest.mark.parametrize("q,name", [
    ("Can I take Bone Builder if I am on warfarin?", "Bone Builder"),
    ("Is Dental Powder safe with a blood thinner?", "Dental Powder"),
])
def test_a_warfarin_question_links_the_caution(appmod, q, name):
    # Glen, 2026-10-24: "Chat doesn't duplicate the caution." A safety question gets the link.
    block = appmod.named_product_facts_block(q)
    link = appmod._CAUTION_LINK_LINE.format(name=name, where=appmod._CAUTION_LINK_WHERE)
    assert f"### {name}" in block
    assert block.count(link) == 1
    assert "has no caution on its product page" not in block
    assert appmod._caution_subjects_asked(q)


@pytest.mark.parametrize("slug", [s for s, _ in PRODUCTS])
def test_the_store_page_shows_the_caution(appmod, monkeypatch, slug):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"description": "", "ingredients": [],
                                                           "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    assert next(s for s in d["sections"] if s["id"] == "caution")["body"] == WARNING
