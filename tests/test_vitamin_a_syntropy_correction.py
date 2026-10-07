"""Vitamin A Syntropy carries its own label, not a gut formula.

Production's store correction, approved by Glen 2026-09-30 ("yes"):
production/05 Formulations/vitamin-a-syntropy/2026-09-30/spec.md and store-correction.json.
The page served a 21-line gut and H. pylori list with no vitamin A: an old Formulations row
named "... Vitamin U Complex" had been matched as "Vitamin A".
"""
import importlib
import json

import pytest

SLUG = "vitamin-a-syntropy"
GUT = ("mastic", "bismuth", "berberine", "slippery elm", "vitamin u", "intestinal lining", "dgl")
WARNING = ("1 a day for one month for low Vitamin A. Up to 3 a day short term for Phase 1 terrain. "
           "Avoid during pregnancy or when planning a pregnancy.")
DIRECTIONS = "1 capsule 1 to 3 times daily with food, or as guided."


@pytest.fixture(scope="module")
def catalog():
    return json.load(open("data/products.json"))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open("data/products-manual-corrections.json"))


def test_the_label_panel_replaces_the_gut_list(catalog):
    p = catalog[SLUG]
    assert len(p["ingredients"]) == 26
    assert p["ingredients_source"] == "label-read-2026-09-30"
    assert any("vitamin a" in i["name"].lower() for i in p["ingredients"])
    assert all(i["dose"] for i in p["ingredients"])
    text = json.dumps(p).lower()
    for term in GUT:
        assert term not in text, term


def test_directions_and_warning_are_glens_words(catalog):
    assert catalog[SLUG]["directions"] == DIRECTIONS
    assert catalog[SLUG]["warning"] == WARNING


def test_the_copy_is_pinned_and_the_ai_explainer_emptied(catalog):
    p = catalog[SLUG]
    assert set(p["copy_pinned"]) == {"ingredients", "intro", "description", "research", "benefits"}
    assert p["how_it_works"] == ""
    assert "note" not in p


def test_name_price_and_filemaker_id_untouched(catalog):
    p = catalog[SLUG]
    assert p["name"] == "Vitamin A Syntropy"  # renamed with the other five Synergy names
    assert p["price_cents"] == 7000
    assert p["fmp_id"] == "539"
    assert p["bottle_type"] == "30 Caps"


def test_the_corrections_file_holds_the_same_panel(catalog, corrections):
    """Enrichment reads this file, so a later run cannot restore the gut list."""
    c = corrections[SLUG]
    for k in ("ingredients", "ingredients_source", "description", "directions", "warning"):
        assert c[k] == catalog[SLUG][k], k
    assert c["note"]


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(appmod, "_product_how", lambda p: "")
    # With AI copy on (as in prod), an unpinned section is marked "ai" and replaced.
    monkeypatch.setattr(appmod, "_SALES_AI_COPY_ENABLED", True)
    return appmod


def test_the_page_serves_the_label_and_pinned_copy(a, catalog):
    c = a.app.test_client()
    data = c.get("/begin/product-page-data/" + SLUG).get_json()
    secs = {s["id"]: s for s in data["sections"]}
    assert secs["intro"]["body"] == catalog[SLUG]["intro"]
    for sid in ("intro", "description", "research"):
        if sid in secs:
            assert "ai" not in secs[sid], sid
    blob = json.dumps(data).lower()
    for term in GUT:
        assert term not in blob, term
    assert WARNING.lower() in blob
    assert DIRECTIONS.lower() in blob
    buy = c.get("/begin/product-data/" + SLUG).get_json()
    assert not buy.get("benefits")
