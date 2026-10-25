"""B17 Syntropy's warning and dose (Glen, 2026-10-25, "yes" to each, formulation tab).

Source: production/05 Formulations/b17-syntropy/2026-10-25/b17-syntropy-warning-and-dose.md.
Each capsule of run 188 holds about 57,600 IU of vitamin A, so the only dose is one a day.
The old store copy said to decrease the dose if lightheaded, an early sign of cyanide
toxicity; the warning says to stop and call Poison Control. Glen approved the
pregnancy sentence's wording in the marketing tab the same day.
"""
import json
import os

import pytest

import app

SLUG = "b17-syntropy"
DIRECTIONS = "1 capsule a day with food, or as guided."
WARNING = (
    "Amygdalin can release cyanide in the body. Stop at the first sign of headache, "
    "dizziness, lightheadedness, weakness, nausea, vomiting, confusion, chest tightness or "
    "unusual breathing, and call Poison Control, 1-800-222-1222. Call 911 for breathing "
    "difficulty, fainting, seizure, bluish lips or loss of consciousness. Do not combine "
    "with apricot kernels, bitter almonds, fruit pits, beta-glucosidase or high-dose "
    "vitamin C. Do not use it in pregnancy or while breastfeeding, or give it to "
    "children. Consult a physician and pharmacist before use with any medication or medical treatment, especially "
    "diabetes medicines or blood thinners. Do not take other vitamin A supplements with "
    "this product. Keep out of reach of children.")


def test_the_record_carries_the_approved_warning_and_pinned_dose():
    p = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                    "products.json")))["products"][SLUG]
    assert p["warning"] == WARNING and p["directions"] == DIRECTIONS
    assert p["copy_pinned"] == ["directions"]
    # Run 188 is retinyl acetate, as the printed label says (formulation, 2026-10-25).
    assert p["ingredients"][0] == {"name": "Vitamin A (as retinyl acetate)", "dose": "56,800 IU"}


@pytest.mark.parametrize("q", ["How much B17 Syntropy should I take?",
                               "i feel lightheaded on b17 syntropy",
                               "Can I take 10 B17 Syntropy a day?"])
def test_the_chat_gets_one_dose_and_the_whole_warning(q):
    b = app._named_product_facts(q)
    assert "### B17 Syntropy" in b
    assert "Directions: " + DIRECTIONS in b
    assert WARNING in b


def test_the_page_shows_the_dose_and_the_caution(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    d = app.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = {s["id"]: s for s in d["sections"]}
    assert sec["caution"]["body"] == WARNING
    assert sec["ingredients"]["body"]["directions"] == DIRECTIONS


def test_content_since_is_a_full_timestamp():
    # Caches store UTC times, and "2026-10-25T21:50Z" sorts after a bare "2026-10-25",
    # so a date alone kept that day's drafts (review round 1, 2026-10-25).
    p = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                    "products.json")))["products"][SLUG]
    assert len(p["content_since"]) > 10 and p["content_since"] > "2026-10-25T21:50:00"


def test_the_corrections_replay_keeps_the_approved_record():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    data = os.path.join(os.path.dirname(__file__), "..", "data")
    products = json.load(open(os.path.join(data, "products.json")))["products"]
    corr = json.load(open(os.path.join(data, "products-manual-corrections.json")))
    rec = dict(products[SLUG])
    # What an enrichment run writes back first: the old store panel, no warning or dose.
    rec.update(ingredients=[{"name": "Vitamin A (Retinyl Palmitate)", "dose": "5000 IU"}])
    for k in ("warning", "directions", "copy_pinned", "content_since"):
        rec.pop(k, None)
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    assert p[SLUG]["ingredients"][0] == {"name": "Vitamin A (as retinyl acetate)",
                                         "dose": "56,800 IU"}
    assert p[SLUG]["warning"] == WARNING and p[SLUG]["directions"] == DIRECTIONS
    assert p[SLUG]["copy_pinned"] == ["directions"] and p[SLUG]["content_since"] == products[SLUG]["content_since"]
    assert p[SLUG]["ingredients_source"] == "t33"


def test_the_chat_gets_the_only_dose_rule():
    b = app._named_product_facts("Can I take 10 B17 Syntropy a day?")
    assert "The Directions above are the only dose for B17 Syntropy." in b
    assert "such as up to 10 capsules a day" in b


def test_a_symptom_is_answered_with_stop_and_call():
    b = app._named_product_facts("I feel lightheaded on B17 Syntropy, should I take less?")
    assert app._STOP_AT_SYMPTOM_RULE in b
    # A caution without Poison Control gets no such rule.
    assert app._STOP_AT_SYMPTOM_RULE not in app._named_product_facts(
        "How much Vitamin D Syntropy should I take?")
