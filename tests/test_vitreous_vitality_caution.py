"""Vitreous Vitality's app page and chat carry the store page's caution, dose and panel.

FileMaker 594, run 99. Each capsule supplies about 8,520 mcg RAE (28,400 IU) of vitamin A,
nearly 3 times the adult upper limit, and the app page had no caution and no directions
while the ebook linked buyers to it. Glen said "yes" in the platform tab on 2026-10-26 to
the text he approved for remedymatch.com 315 (formulation spec
production/05 Formulations/vitreous-vitality/2026-10-26/app-store-spec.md). The spec also
takes lutein, which is not in the formula, out of the intro and floaters out of the overview.
"""
import json
import os

import pytest

import app

SLUG = "vitreous-vitality"
DIRECTIONS = "1 capsule daily with food, as guided."
VITAMIN_A = {"name": "Vitamin A (retinyl acetate)",
             "dose": "8,520 mcg RAE (28,400 IU), 947% of the Daily Value"}
CAUTION_START = "Each capsule supplies 8,520 mcg RAE (28,400 IU) of preformed vitamin A."
PREGNANCY = "Do not use if you are pregnant, may become pregnant or are breastfeeding."
CAUTION_END = "Consult first too if you have a bleeding disorder, or before planned surgery. Keep out of reach of children."
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


def test_the_record_carries_the_store_text():
    p = _record()
    assert p["directions"] == DIRECTIONS
    assert p["ingredients"][0] == VITAMIN_A
    assert len(p["ingredients"]) == 34
    assert p["chat_caution"] is True
    w = p["warning"]
    assert w.startswith(CAUTION_START) and PREGNANCY in w and w.endswith(CAUTION_END)
    assert "28,500" not in json.dumps(p)
    for k in ("intro", "description"):
        assert "lutein" not in p[k].lower() and "floater" not in p[k].lower(), k
    names = " ".join(i["name"] for i in p["ingredients"])
    assert "Retinyl Acetate" not in names and "Magnesium L-Threonate" not in names


def test_the_page_data_shows_the_caution_and_directions():
    r = app.app.test_client().get(f"/begin/product-page-data/{SLUG}")
    assert r.status_code == 200
    d = r.get_json()
    s = json.dumps(d)
    assert CAUTION_START in s and PREGNANCY in s and CAUTION_END in s
    assert f'"directions": "{DIRECTIONS}"' in s
    assert '"dose": "8,520 mcg RAE (28,400 IU), 947% of the Daily Value"' in s
    sec = {x["id"]: x for x in d["sections"]}
    for k in ("intro", "description"):
        assert "lutein" not in json.dumps(sec[k]["body"]).lower(), k
        assert "floater" not in json.dumps(sec[k]["body"]).lower(), k
    assert '"10 mg"' not in json.dumps(sec["ingredients"]["body"]["ingredients"][0])


@pytest.mark.parametrize("q", [
    "How many Vitreous Vitality capsules a day?",
    "How much vitamin A is in Vitreous Vitality?",
    "vitreous vitality dose?",
    "Can I take Vitreous Vitality while pregnant?",
])
def test_the_chat_gets_the_dose_and_the_caution(q):
    b = app._named_product_facts(q)
    assert "### Vitreous Vitality\n" in b
    assert f"Directions: {DIRECTIONS}" in b
    assert "- Vitamin A (retinyl acetate): 8,520 mcg RAE (28,400 IU), 947% of the Daily Value" in b
    assert "The Directions above are the only dose for Vitreous Vitality." in b
    assert "such as up to 3 capsules a day" in b
    assert "Give no percent Daily Value that is not listed above" in b
    assert f"Caution: {CAUTION_START}" in b and PREGNANCY in b and CAUTION_END in b
    assert "has no caution on its product page" not in b


def test_the_corrections_replay_restores_the_store_text():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    corr = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))
    approved = _record()
    # What a re-import writes back: the formulation database's raw weights, no text.
    rec = dict(approved, ingredients=[{"name": "Retinyl Acetate", "dose": "10 mg"}],
               directions="", warning="", chat_caution=False, copy_pinned=[],
               intro="Retinyl acetate, zinc carnosine, lutein, and crocin.",
               description="Whether you're concerned about floaters.")
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    for k in ("ingredients", "directions", "warning", "chat_caution", "copy_pinned",
              "intro", "description"):
        assert p[SLUG][k] == approved[k], k
