"""Stress Release carries Glen's medication caution (production store spec
2026-10-24-stress-release-caution, v3; Glen "approve" in production, "yes" in platform).

The chatbot invented a caution for Stress Release on 2026-10-23. This gives it the real
one to quote. `chat_caution` adds the copy rule, the stay line, the closing reminder and
the drug-interaction snippet filter. `stress-release` is a cue name, so a lowercase
"stress release" counts beside a cue word.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WARNING = ("May cause drowsiness. If you take a prescription medication, especially a blood "
           "thinner, a sedative or a thyroid medication, consult your physician before use. "
           "Also consult your physician first if you have an autoimmune or thyroid condition. "
           "Do not use if you are about to have surgery. Avoid use in pregnancy or while "
           "breastfeeding.")
LINE = f"Caution: {WARNING}"


@pytest.fixture(scope="module")
def entry():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]["stress-release"]


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    return a


def test_the_record_carries_the_approved_caution(entry):
    assert entry["warning"] == WARNING
    assert entry["chat_caution"] is True


@pytest.mark.parametrize("q", [
    "is stress release safe with warfarin?",
    "is stress release safe in pregnancy?",
    "stress release dose?",
    "how many stress release capsules can I take a day?",
    "Can I take Stress Release before surgery?",
])
def test_the_block_quotes_the_caution_once_with_its_rules(appmod, q):
    block = appmod.named_product_facts_block(q)
    assert "### Stress Release" in block
    assert block.count(LINE) == 1
    assert "Caution: Caution:" not in block
    assert appmod._CAUTION_STAY_LINE in block
    assert appmod._CAUTION_CLOSING_REMINDER in block
    assert "has no caution on its product page" not in block


def test_the_warfarin_question_names_a_caution_subject(appmod):
    assert appmod._caution_subjects_asked("is stress release safe with warfarin?")


# Known misses, left as they are (spec): "while" and "before" are not cue words.
@pytest.mark.parametrize("q", ["can I take stress release while pregnant?",
                               "can i take stress release before surgery?"])
def test_known_misses_get_no_block(appmod, q):
    assert "### Stress Release" not in appmod.named_product_facts_block(q)


@pytest.mark.parametrize("q", [
    "I need some stress release this week",
    "What are safe ways to get stress release this week?",
    "Is Iron Out safe? I also need some stress release this week",
])
def test_everyday_phrases_bring_no_stress_release_caution(appmod, q):
    block = appmod.named_product_facts_block(q)
    assert WARNING not in block
    assert "### Stress Release" not in block


def test_iron_out_keeps_its_own_block(appmod):
    block = appmod.named_product_facts_block(
        "Is Iron Out safe? I also need some stress release this week")
    assert "### Iron Out" in block


# Accepted matches: Glen accepted these lowercase false positives in exchange for
# lowercase coverage (production tab, 2026-10-24, "yes"). Pinned here so a later change
# to the matcher sees them.
@pytest.mark.parametrize("q", [
    "is tapping for stress release safe?",
    "how much stress release do I need from exercise?",
    "is emotional stress release safe?",
    "what is stress release?",
])
def test_accepted_false_positives_bring_the_block(appmod, q):
    block = appmod.named_product_facts_block(q)
    assert "### Stress Release" in block and LINE in block


def test_the_store_page_shows_the_caution(appmod, monkeypatch):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"description": "", "ingredients": [],
                                                           "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    d = appmod.app.test_client().get("/begin/product-page-data/stress-release").get_json()
    ing = next(s for s in d["sections"] if s["id"] == "ingredients")
    assert ing["body"]["warning"] == WARNING
