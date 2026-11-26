"""Nerve Pulse matches its published store page (run 140). Glen "yes", 2026-11-26, platform tab."""
import json
import os

import pytest

import app

ROOT = os.path.join(os.path.dirname(__file__), "..")
DIRS = "1 capsule up to 3 times daily with food, or as guided. Take it one week on, then one week off."
CAUTION_HEAD = "St. John's wort interacts with many medicines."
CAUTION_TAIL = "Stop 2 weeks before planned surgery. Keep out of reach of children."


def _rec(f):
    d = json.load(open(os.path.join(ROOT, "data", f)))
    return (d["products"] if f == "products.json" else d)["nerve-pulse"]


@pytest.mark.parametrize("f", ["products.json", "products-manual-corrections.json"])
def test_the_record_is_the_store_page(f):
    p = _rec(f)
    assert p["directions"] == DIRS
    assert p["warning"].startswith(CAUTION_HEAD) and p["warning"].endswith(CAUTION_TAIL)
    assert ("Huperzine A 5% (Huperzia serrata), supplying 35 mcg huperzine A", "700 mcg") in [
        (i["name"], i["dose"]) for i in p["ingredients"]]
    assert set(p["copy_pinned"]) == {"description", "directions", "ingredients"}
    assert p["chat_caution"] is True


@pytest.mark.parametrize("q", [
    "What is in Nerve Pulse?",
    "How do I take Nerve Pulse?",
])
def test_a_contents_or_dose_answer_carries_the_whole_caution(q):
    block = app.named_product_facts_block(q)
    assert f"Caution: {_rec('products.json')['warning']}" in block
    assert DIRS in block


@pytest.mark.parametrize("q", [
    "Can I take Nerve Pulse while pregnant?",
    "can i take nerve pulse while pregnant?",
    "is nerve pulse safe with sertraline?",
    "is nerve pulse safe while breastfeeding?",
    "Can I take Nerve Pulse before surgery?",
])
def test_a_safety_question_in_any_case_reaches_the_caution(q):
    assert "nerve-pulse" in app._caution_link_slugs(q)
    assert "### Nerve Pulse" in app.named_product_facts_block(q)


def test_its_pregnancy_sentence_keeps_the_any_case_match():
    p = app._PRODUCTS["products"]["nerve-pulse"]
    assert app._PREGNANCY_LINE_WITHIN["nerve-pulse"] in p["warning"]
    assert app._carries_pregnancy_line("nerve-pulse", p)


def test_a_lowercase_name_without_a_safety_word_still_names_nothing():
    assert "### Nerve Pulse" not in app.named_product_facts_block("my nerve pulse feels fast")
