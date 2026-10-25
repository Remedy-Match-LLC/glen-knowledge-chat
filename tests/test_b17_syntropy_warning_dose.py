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
    "children. Consult a physician and pharmacist before use with any medication or cancer treatment, especially "
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
