"""Pins the N-Acetyl Cysteine Pure Powder catalog entry, set 2026-09-13.

Glen approved a 180 g powder label with a 500 mg scoop and asked for the size on the
sales page. The page has no size field, and its prose sections are replaced by the cached
AI draft, so the size and the dose live in the deterministic label lines served with the
ingredients panel: the ingredient row, `directions` and `panel_note`.

`bottle_type` is the shipping packing key, not display text. "120 caps" is the bottle
this powder ships in, the same one WholOmega 120 uses.

The price is deliberately unchanged, on Glen's instruction.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(ROOT, "data", "products.json")
CORRECTIONS = os.path.join(ROOT, "data", "products-manual-corrections.json")

SLUG = "nacetyl-cysteine"
INGREDIENTS = [("N-Acetyl Cysteine (NAC)", "500 mg per scoop")]
DIRECTIONS = ("Take 1 scoop (500 mg) 1 to 2 times daily on an empty stomach, "
              "up to 2 scoops twice daily short term, or as guided.")
PANEL_NOTE = "180 g Pure Powder, about 360 scoops."


@pytest.fixture(scope="module")
def products():
    return json.load(open(PRODUCTS))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open(CORRECTIONS))


def test_catalog_carries_the_label_lines(products):
    p = products[SLUG]
    assert [(i["name"], i["dose"]) for i in p["ingredients"]] == INGREDIENTS
    assert p["directions"] == DIRECTIONS
    assert p["panel_note"] == PANEL_NOTE
    assert p["bottle_type"] == "120 caps"


def test_name_and_price_are_untouched(products):
    """The name is the QuickBooks invoice identity, and Glen kept the price."""
    p = products[SLUG]
    assert p["name"] == "N-Acetyl Cysteine"
    assert p["price_cents"] == 3997


def test_correction_is_recorded_so_enrichment_cannot_revert_it(corrections, products):
    c = corrections[SLUG]
    assert [(i["name"], i["dose"]) for i in c["ingredients"]] == INGREDIENTS
    for field in ("directions", "panel_note", "bottle_type"):
        assert c[field] == products[SLUG][field], field


def test_the_scoop_count_matches_the_size():
    assert 180_000 / 500 == 360


def test_page_data_serves_size_and_dose(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    # app.py refuses to import without these; the test never calls either service.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    data = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    body = next(s for s in data["sections"] if s["id"] == "ingredients")["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == INGREDIENTS
    assert body["directions"] == DIRECTIONS
    assert body["note"] == PANEL_NOTE
    assert not body["warning"]
