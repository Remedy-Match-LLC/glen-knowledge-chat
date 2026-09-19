"""Fulvic Acid is one ingredient: 500 mg of Fulvic Acid 95%.

Glen, 2026-09-18: "yes, 500 mg Fulvic Acid 95% only".

WHAT WAS WRONG. The catalog listed 650 mg Fulvic Acid plus a vitamin A line. Both came
from t33 row FOR000036, a concept note for one person, not this product. The 2026-09-17
fix named the vitamin A form correctly, but the line should never have been there.

SOURCE. FileMaker raw 3519 'Fulvic Acid 95%' (Supernal Sublime): dosage_standard 500 mg,
range 1000-2000 mg/day.

NOT RULED, so not touched here: directions and bottle_type.
"""
import json
import pathlib

CATALOG = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "data" / "products.json").read_text()
)["products"]
P = CATALOG["fulvic-acid"]


def test_one_ingredient_at_the_ruled_dose():
    assert P["ingredients"] == [{"name": "Fulvic Acid 95%", "dose": "500 mg"}]


def test_no_vitamin_a_line_in_any_form():
    assert not any("vitamin a" in (i.get("name") or "").lower() for i in P["ingredients"])


def test_source_is_no_longer_the_t33_concept_note():
    assert P["ingredients_source"] == "manual"


def test_the_ruling_is_recorded_on_the_product():
    note = P.get("enrichment_note") or ""
    assert "2026-09-18" in note and "500 mg Fulvic Acid 95% only" in note, note
    assert "FOR000036" in note


def test_identity_untouched():
    """Slug, name and price carry the QuickBooks identity."""
    assert P["name"] == "Fulvic Acid" and P["price_cents"] == 3997


def test_its_sibling_still_carries_glens_own_correction():
    """humic-acid was corrected by Glen for a bad 'Fulvic Acid Complex' match."""
    note = CATALOG["humic-acid"].get("enrichment_note") or ""
    assert "Fulvic Acid Complex" in note and "wrong" in note
