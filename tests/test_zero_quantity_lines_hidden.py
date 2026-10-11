"""FileMaker zero-quantity lines are kept as 0 mg notes and hidden from customers.

Glen's rule, 2026-09-09: a BOM line with zero quantity is a proposed future enhancement
and must not be listed publicly. A blank dose is NOT hidden by shown_ingredients, so
these lines showed live. formulation-1b confirmed them 2026-10-10 against the 10-05
FileMaker extract by count elimination (blank = zero count, dosed = dosed count).
"""
import json

import pytest

from dashboard.products import shown_ingredients

SHOWN = {"brain-cleanse": 9, "glutathione-syntropy": 9, "neuroprotect": 8,
         "stress-release": 16, "snake--dragon": 10, "soothe-powder": 14,
         "scar-soft-drink": 9}


@pytest.mark.parametrize("slug", sorted(SHOWN))
def test_no_blank_dose_and_zero_lines_hidden(slug):
    p = json.load(open("data/products.json"))["products"][slug]
    assert not [i["name"] for i in p["ingredients"] if not str(i.get("dose", "")).strip()]
    assert len(shown_ingredients(p["ingredients"])) == SHOWN[slug]


def test_copy_and_image_prompts_skip_hidden_lines():
    # A hidden 0 mg line must not reach generated sales copy or image prompts either.
    from dashboard import sales_copy, sales_image_prompts
    p = {"name": "X", "ingredients": [{"name": "Shown Root", "dose": "50 mg"},
                                      {"name": "Hidden Saffron", "dose": "0 mg"}]}
    assert "Hidden Saffron" not in sales_copy._ingredient_lines(p)
    assert "Shown Root" in sales_copy._ingredient_lines(p)
    assert "- Hidden Saffron" not in sales_image_prompts._ingredient_lines(p)
    assert "- Shown Root" in sales_image_prompts._ingredient_lines(p)
