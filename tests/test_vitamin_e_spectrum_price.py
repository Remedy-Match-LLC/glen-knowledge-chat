"""Vitamin E Spectrum sells at the Functional Formulation price.

It sat at $39.97 from the catalogue expansion of 2026-05-30 (c92f5890). Glen, primary tab
2026-10-02: "Vitamin E Spectrum is $70, same as other FFs". FileMaker 340 is a 30-gelcap
Functional Formulation. 6997 now; the whole-dollar pass takes it to $70 with the rest.
"""
import json
from pathlib import Path

PRODUCTS = json.loads((Path(__file__).parents[1] / "data" / "products.json").read_text())["products"]


def test_vitamin_e_spectrum_is_priced_like_the_other_ffs():
    assert PRODUCTS["vitamin-e-spectrum"]["price_cents"] == 6997


def test_the_ff_it_is_matched_to_still_holds_that_price():
    """The control: if the comparison product moved, this test's premise is stale."""
    assert PRODUCTS["nous-energy"]["price_cents"] == 6997
    assert PRODUCTS["nous-energy"]["bottle_type"] == PRODUCTS["vitamin-e-spectrum"]["bottle_type"]
