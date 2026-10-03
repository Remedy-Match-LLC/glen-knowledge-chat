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


def test_vitamin_e_spectrum_carries_the_ff_flag():
    """Round 1: at the FF price without the flag, buyers paid $69.97 with none of the FF
    volume or member discounts. Glen said yes to treating it fully as an FF, 2026-10-02."""
    assert PRODUCTS["vitamin-e-spectrum"].get("qty_pricing") is True


def test_the_scar_bundle_follows_its_rule_at_the_new_price():
    """Scar Reduction Program is 10% off its four parts; Glen accepted $251.89, 2026-10-02."""
    assert PRODUCTS["scar-reduction-program"]["price_cents"] == 25189
