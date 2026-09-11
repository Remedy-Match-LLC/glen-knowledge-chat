"""Cistus Shield's dosing must render on the product page.

Glen, 2026-09-10: "Build up to 2 capsules 3 times a day according to tolerance."
He confirmed separately that "with food" stays.

#1620 put that line in `description`, which is where the old line sat and what the
spec named. `description` is not what the product page shows. The page reads
`body.directions` from /begin/product-page-data and renders it under the ingredient
list (static/begin-product.html, .sp-ing-directions). app.py builds that from
`p.get("directions", "")`.

Verified against production after #1620 went live: the page returned 8 ingredients and
`directions: ''`, so the dosing Glen wrote was invisible to customers.

`description` is kept as well. It is a different consumer (product_content, the
chatbot's content layer), and dropping it would be a second, unasked change.
"""
import json

import pytest

SHIELD = "cistus-shield"
DOSING = "Build up to 2 capsules 3 times a day according to tolerance, with food."


@pytest.fixture(scope="module")
def catalog():
    return json.load(open("data/products.json"))["products"]


def test_the_capsules_carry_dosing_in_the_field_the_page_renders(catalog):
    assert catalog[SHIELD].get("directions") == DOSING


def test_the_description_still_carries_it_too(catalog):
    """Pins the #1620 value. This test passed before this change and must keep passing."""
    assert catalog[SHIELD].get("description") == DOSING


def test_every_product_with_dosing_uses_the_same_field(catalog):
    """`directions` is the one dosing field. A product must never carry dosing ONLY in
    `description`, which is what made Glen's line invisible."""
    for slug, p in catalog.items():
        desc = (p.get("description") or "").strip()
        looks_like_dosing = desc.lower().startswith(("take ", "mix ", "build up to "))
        if looks_like_dosing:
            assert (p.get("directions") or "").strip(), \
                f"{slug} has dosing in description but nothing in directions"
