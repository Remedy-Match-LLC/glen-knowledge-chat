"""Glen, 2026-09-16: the catalog name must match the FileMaker record, which he
created as "Myelin Syntropy Powder" — consistent with his other powders (TMG
Syntropy Powder, Mucosa Syntropy Powder, Mercury Detox Syntropy Powder).

It is not cosmetic. `biofield_local_app.author_invoice` looks up doses_per_bottle in
FileMaker BY PRODUCT NAME, using the remedy name on the report layer. A report saying
"Myelin Syntropy" finds nothing against a FileMaker record named "Myelin Syntropy
Powder", and the bottle count silently falls back to 1 — one jar for a month that
needs three.
"""
import json
import os

import pytest


@pytest.fixture(scope="module")
def products():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "products.json")
    return json.load(open(path))["products"]


def test_the_catalog_name_matches_the_filemaker_record(products):
    assert products["myelin-syntropy"]["name"] == "Myelin Syntropy Powder"


def test_the_slug_is_unchanged(products):
    """Five client reports had their reorder baskets pointed at this slug on
    2026-09-16. Renaming it would break every one of them, so only the display name
    moves."""
    assert "myelin-syntropy" in products
    assert "myelin-syntropy-powder" not in products


def test_the_price_did_not_move(products):
    assert products["myelin-syntropy"]["price_cents"] == 6997


def test_the_predecessors_are_untouched_by_this_change(products):
    """Retiring them is a separate decision, still with Glen."""
    assert products["neuroceramides"]["name"] == "Neuroceramides"
    assert products["myelin-repair-neuroceramides"]["name"] == "Myelin Repair Neuroceramides"
