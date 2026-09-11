"""Cistus Shield's ingredient amounts must say what they are amounts OF.

Glen confirmed 2026-09-10 that the figures are PER CAPSULE. #1620 shipped them bare.

WHY BARE IS NOT GOOD ENOUGH HERE. The product page prints each dose with no basis
(`static/begin-product.html` renders `body.ingredients[].dose` straight into a list under
"What's inside"). The record sitting immediately next to this one in the catalogue,
`cistus-syntropy-immunitea`, states its amounts PER SERVING, in its own description: "Each
serving of Cistus Synergy as a delicious hot or cold tea supplies". So two neighbouring
records use different bases and a reader comparing them is comparing a capsule against a
serving without being told.

THE EVIDENCE, checked two independent ways rather than taken on report:

  * FMP BOM for product 1193: every Raw line's `zc_qty_total` is exactly 30x its `qty`
    (Cryptolepis 83 and 2490, PEA 100 and 3000, and so on) against a 30-capsule batch.
  * `ingredients.db` `product_ingredients` for product 1193 carries a ninth line,
    `Plantcaps®`, at `qty=1.0 ea.`. ONE capsule shell per unit is what makes the column
    per capsule, and it settles the question without any arithmetic at all.

The eight actives sum to exactly 433 mg.

THE CONVENTION THIS FOLLOWS. `fibrolysis-factors` says "five standardized actives at
487.5 mg per capsule" and `estro-clear` says "nine actives at about 497 mg per capsule",
both in `description`, both with dosing in `directions`. This record now matches.
"""
import json

import pytest

SHIELD = "cistus-shield"
POWDER = "cistus-syntropy-immunitea"
DOSING = "Build up to 2 capsules 3 times a day according to tolerance, with food."


@pytest.fixture(scope="module")
def catalog():
    return json.load(open("data/products.json"))["products"]


def test_the_amounts_are_declared_per_capsule(catalog):
    assert "per capsule" in (catalog[SHIELD].get("description") or "").lower()


def test_the_declared_total_matches_the_ingredient_array(catalog):
    """The stated number must be derived from the array, never typed beside it. A total
    that drifts from its own ingredient list is worse than no total."""
    p = catalog[SHIELD]
    total = sum(int(i["dose"].split()[0]) for i in p["ingredients"])
    assert total == 433
    assert str(total) in p["description"]
    assert str(len(p["ingredients"])) not in ("", None)


def test_dosing_still_lives_in_directions_and_is_glens_wording(catalog):
    """Pins #1629. The basis line goes in `description`; it must not displace the dosing,
    which is what the page renders as 'Suggested use'."""
    assert catalog[SHIELD].get("directions") == DOSING


def test_the_neighbour_still_says_per_serving(catalog):
    """The reason this matters. If the powder's wording ever changes, the ambiguity this
    test guards against changes shape, and someone should look again."""
    assert "each serving" in (catalog[POWDER].get("description") or "").lower()


def test_a_capsule_product_that_states_a_total_states_its_basis(catalog):
    """General guard. Three capsule products now quote an actives total in their
    description. Quoting a number without saying what it is a number of is the defect
    this file exists for, so a fourth must not appear silently."""
    import re
    for slug, p in catalog.items():
        desc = (p.get("description") or "")
        if re.search(r"\bactives at\b", desc, re.I):
            assert re.search(r"per (capsule|serving|scoop|dose)", desc, re.I), \
                f"{slug} quotes an actives total with no basis"
