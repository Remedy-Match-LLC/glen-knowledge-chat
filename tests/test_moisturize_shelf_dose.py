"""Moisturize's dose and vitamin A follow the bottles on the shelf (Glen, 2026-10-25).

FileMaker 364. Run 176 (May 2026) weighed 3.3 mg of 98% retinyl acetate a capsule, about
9,400 IU (2,820 mcg RAE), against the label's 5,000 IU. The label's "up to 3 times daily"
would be about 28,000 IU against a 10,000 IU adult upper limit. Glen approved "1 capsule
daily with food, or as guided." and, in the platform tab, "show what we are supplying now"
for the May bottles. The next batch is 1.75 mg (5,000 IU): this file changes when it ships.
"""
import json
import os

import app

SLUG = "moisturize"
DIRECTIONS = "1 capsule daily with food, or as guided."
VITAMIN_A = {"name": "Vitamin A (Retinyl Acetate)", "dose": "9400 IU (2820 mcg RAE)"}
TEXT_FIELDS = ("description", "intro", "learn_markdown", "directions")
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


def test_the_record_shows_the_shelf_batch():
    p = _record()
    assert p["directions"] == DIRECTIONS
    assert p["ingredients"][0] == VITAMIN_A
    assert "Vitamin A (Retinyl Acetate) 9400 IU (2820 mcg RAE);" in p["learn_markdown"]
    for k in TEXT_FIELDS:
        assert "3 times" not in p[k], k
        assert "5000 IU" not in p[k], k
    for k in ("description", "intro", "learn_markdown"):
        assert "Take 1 capsule daily with food, or as guided." in p[k], k


def test_the_page_data_shows_one_a_day_and_the_shelf_vitamin_a():
    """/begin/product-page-data is what the product page renders from."""
    r = app.app.test_client().get(f"/begin/product-page-data/{SLUG}")
    assert r.status_code == 200
    s = json.dumps(r.get_json())
    assert "Take 1 capsule daily with food, or as guided." in s
    assert '"directions": "1 capsule daily with food, or as guided."' in s
    assert '"dose": "9400 IU (2820 mcg RAE)"' in s
    assert "3 times" not in s and "5000 IU" not in s


def test_the_chat_brings_no_moisturize_block():
    """Moisturize is an everyday word, so the chat's dose comes from the knowledge base
    (knowledge-69 carries it). This pins that, so a future block cannot quietly differ."""
    assert app._named_product_facts("How many Moisturize capsules should I take?") == ""


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
    rec["directions"] = "1 capsule up to 3 times daily with food or as guided."
    rec.pop("content_since", None)
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    for k in TEXT_FIELDS + ("ingredients", "content_since"):
        assert p[SLUG][k] == approved[k], k
