"""Moisturize's dose and vitamin A follow the bottles on the shelf (Glen, 2026-10-25).

FileMaker 364. Run 176 (May 2026) weighed 3.3 mg of 98% retinyl acetate a capsule, about
9,400 IU (2,820 mcg RAE), against the label's 5,000 IU. The label's "up to 3 times daily"
would be about 28,000 IU against a 10,000 IU adult upper limit. Glen approved "1 capsule
daily with food, or as guided." and, in the platform tab, "show what we are supplying now"
for the May bottles. The next batch is 1.75 mg (5,000 IU): this file changes when it ships.
On 2026-10-26 Glen said "yes" to the store page's caution and run 176 panel for the app.
"""
import json
import os

import pytest

import app

SLUG = "moisturize"
DIRECTIONS = "1 capsule daily with food, or as guided."
VITAMIN_A = {"name": "Vitamin A (retinyl acetate)",
             "dose": "2,820 mcg RAE (9,400 IU), 313% of the Daily Value"}
CAUTION_START = "Each capsule supplies about 9,400 IU (2,820 mcg RAE) of preformed vitamin A."
CAUTION_END = "Also consult first if you take blood-thinning medicines. Keep out of reach of children."
TEXT_FIELDS = ("description", "intro", "learn_markdown", "directions")
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


def test_the_record_shows_the_shelf_batch():
    p = _record()
    assert p["directions"] == DIRECTIONS
    assert p["ingredients"][0] == VITAMIN_A
    assert ("Vitamin A (retinyl acetate) 2,820 mcg RAE (9,400 IU), 313% of the Daily Value;"
            in p["learn_markdown"])
    assert [i["name"] for i in p["ingredients"]][4:] == [
        "Green tea extract, polyphenols 98%, EGCG 50% (Camellia sinensis)",
        "Maqui anthocyanidins 25% (Aristotelia chilensis)"]
    assert "panel_note" not in p
    assert p["chat_caution"] is True
    assert p["warning"].startswith(CAUTION_START) and p["warning"].endswith(CAUTION_END)
    for k in TEXT_FIELDS:
        assert "3 times" not in p[k], k
        assert "5000 IU" not in p[k] and "5,000 IU" not in p[k], k
        assert "300 mg" not in p[k] and "catechins 80%" not in p[k], k
    for k in ("description", "intro", "learn_markdown"):
        assert "Take 1 capsule daily with food, or as guided." in p[k], k


def test_the_page_data_shows_one_a_day_and_the_shelf_vitamin_a():
    """/begin/product-page-data is what the product page renders from."""
    r = app.app.test_client().get(f"/begin/product-page-data/{SLUG}")
    assert r.status_code == 200
    s = json.dumps(r.get_json())
    assert "Take 1 capsule daily with food, or as guided." in s
    assert '"directions": "1 capsule daily with food, or as guided."' in s
    assert '"dose": "2,820 mcg RAE (9,400 IU), 313% of the Daily Value"' in s
    assert "3 times" not in s and "5000 IU" not in s
    assert CAUTION_START in s and CAUTION_END in s


@pytest.mark.parametrize("q", [
    "How many Moisturize capsules a day?",
    "How much vitamin A is in Moisturize?",
    "Can I take 2 Moisturize a day?",
    "Moisturize dose?",
    # Lowercase beside a product cue (review round 2, 2026-10-26).
    "moisturize dose?",
    "how many moisturize capsules can I take?",
    "is moisturize safe?",
    # A sentence-start name with "'s", a product word or alone (review round 1).
    "Moisturize's dose?",
    "Moisturize supplement?",
    "Moisturize?",
    "How Many Moisturize Capsules A Day?",
])
def test_the_chat_gets_one_a_day_and_the_shelf_vitamin_a(q):
    """With no block the bot found no dose, and live it added a second capsule, about
    18,800 IU of vitamin A (log 2370, 2026-10-26)."""
    b = app._named_product_facts(q)
    assert "### Moisturize\n" in b
    assert f"Directions: {DIRECTIONS}" in b
    assert ("- Vitamin A (retinyl acetate): 2,820 mcg RAE (9,400 IU), 313% of the Daily Value"
            in b)
    assert f"Caution: {CAUTION_START}" in b and CAUTION_END in b
    assert "The Directions above are the only dose for Moisturize." in b
    assert "never give it, even when the user asks about taking more" in b
    assert "Give no percent Daily Value that is not listed above" in b
    assert "5000 IU" not in b
    assert "has no caution on its product page" not in b


@pytest.mark.parametrize("q", [
    "Moisturize my skin. What helps?",
    "how do I moisturize dry skin?",
    "What helps me moisturize my eyes?",
    "how much water should I drink to moisturize my skin?",
    "what oils moisturize skin best?",
    "How do I moisturize my eyes? Is it safe?",
    # Title case, a later one-word sentence, and a product word that runs on (review
    # round 3, 2026-10-26).
    "How Do I Moisturize Dry Skin?",
    "Should I Moisturize Twice A Day?",
    "My skin is so dry. Moisturize?",
    "Dry skin. Moisturize!",
    "Moisturize products for my face?",
    "Moisturize supplements that work?",
])
def test_the_everyday_word_brings_no_block(q):
    assert "### Moisturize\n" not in app._named_product_facts(q) + "\n"


def test_no_amount_rule_for_another_product():
    b = app._named_product_facts("How much B17 Syntropy should I take?")
    assert "### B17 Syntropy" in b and "Give no percent Daily Value" not in b


def test_a_long_message_is_read_quickly():
    import time
    t = time.monotonic()
    app._named_product_facts("Moisturize? " * 20000)
    app._named_product_facts("Clarity? " * 20000)
    assert time.monotonic() - t < 5


def test_the_directions_are_pinned_for_the_chat():
    p = _record()
    assert "directions" in p["copy_pinned"]
    corr = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))[SLUG]
    assert corr["copy_pinned"] == p["copy_pinned"]


def test_the_corrections_replay_keeps_the_shelf_record():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    corr = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))
    approved = _record()
    old = "Take 1 capsule up to 3 times daily with food or as guided."
    rec = dict(approved)
    # What a re-import writes back first: the label's panel and dose.
    rec["ingredients"] = [{"name": VITAMIN_A["name"], "dose": "5000 IU"}] + [
        dict(i) for i in approved["ingredients"][1:]]
    for k in ("description", "intro", "learn_markdown"):
        rec[k] = approved[k].replace("Take 1 capsule daily with food, or as guided.", old)
    rec["learn_markdown"] = rec["learn_markdown"].replace(
        "Vitamin A (retinyl acetate) 2,820 mcg RAE (9,400 IU), 313% of the Daily Value;",
        "Vitamin A (Retinyl Acetate) 5000 IU;")
    assert "5000 IU" in rec["learn_markdown"]
    rec["directions"] = "1 capsule up to 3 times daily with food or as guided."
    rec.pop("content_since", None)
    rec.pop("warning", None)
    rec["chat_caution"] = False
    rec["copy_pinned"] = [k for k in approved["copy_pinned"] if k != "directions"]
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    for k in TEXT_FIELDS + ("ingredients", "content_since", "copy_pinned", "warning",
                            "chat_caution"):
        assert p[SLUG][k] == approved[k], k
