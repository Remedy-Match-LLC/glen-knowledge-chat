"""Three TMG catalog entries become two, and `tmg` becomes the pure powder.

Glen, 2026-09-16: "Merge to one Syntropy blend, and this is the updated pure powder
($39.97)." He confirmed the 150 g pure powder has been produced, which is what released
the page: a customer surface must not move ahead of what exists.

Spec: production/05 Formulations/tmg-powder/2026-09-16/catalog-merge-spec.md

Three entries described one product. `tmg-syntropy-powder` and
`tmg-syntropy-powder-trimethylglycine` were the same blend at the same price pointing at
the same page, and `tmg` was the same blend again at a lower one.

No slug changes and no name changes anywhere, deliberately. The name is the QuickBooks
invoice identity, and a renamed SKU keeps its old slug regardless, so leaving both alone
is what keeps this cheap and keeps every existing link working.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRODUCTS = ROOT / "data" / "products.json"

BLEND = "tmg-syntropy-powder"
RETIRED = "tmg-syntropy-powder-trimethylglycine"
PURE = "tmg"


@pytest.fixture(scope="module")
def products():
    return json.loads(PRODUCTS.read_text())["products"]


def test_the_surviving_blend_keeps_everything_that_was_only_on_it(products):
    b = products[BLEND]
    assert b["fmp_id"] == "368"
    assert b["bottle_type"] == "30 g"
    assert b["regular_cents"] == 8000
    assert b["no_groovekart"] is True
    assert b["price_cents"] == 6997


def test_the_surviving_blend_inherits_what_was_only_on_the_duplicate(products):
    """The url and the enrichment note existed on the retired entry alone."""
    b, r = products[BLEND], products[RETIRED]
    assert b["url"] == r["url"]
    assert b["url"].endswith("228-tmg-syntropy-powder-trimethylglycine")
    assert b["enrichment_note"] == r["enrichment_note"]
    assert "Vitamin B15 & B16" in b["enrichment_note"]


def test_the_duplicate_is_retired_and_points_at_its_survivor(products):
    """`inactive` alone strands anyone holding the old slug.

    app.py follows `superseded_by` to the replacement, and every other retired entry in
    this file carries the pair. Without it the slug resolves to nothing.
    """
    r = products[RETIRED]
    assert r["inactive"] is True
    assert r["superseded_by"] == BLEND
    assert r["superseded_by"] in products, "a survivor that does not exist is worse than none"


def test_the_pure_powder_is_one_ingredient_now(products):
    p = products[PURE]
    assert len(p["ingredients"]) == 1, "the blend lines must be gone"
    only = p["ingredients"][0]
    assert only["name"] == "TMG (Trimethylglycine / Anhydrous Betaine)"
    assert only["dose"] == "500 mg per scoop"
    joined = json.dumps(p["ingredients"]).lower()
    for gone in ("pangamic", "dimethylglycine", "mogroside"):
        assert gone not in joined, f"{gone} belongs to the blend, not the pure powder"


def test_the_pure_powder_gains_its_panel_fields(products):
    p = products[PURE]
    assert p["bottle_type"] == "120 caps"
    assert p["panel_note"] == "150 g Pure Powder, about 300 scoops."
    assert p["directions"].startswith("Take 1 to 2 scoops (500 mg to 1 g)")


def test_bottle_type_is_a_container_not_a_dose_count():
    """"120 caps" on a 150 g powder looks wrong and is not.

    `bottle_types` carries diameter_mm and height_mm, and shipping.packing_bottle_type
    uses it to pick a box. It names the JAR, not what is in it, so a powder filling the
    same jar as 120 capsules carries the same value. N-Acetyl Cysteine has done exactly
    this at the same price since before this change.

    This test exists because the contradiction was raised as a defect during review and
    was not one. Without it the next reader "fixes" it and breaks the packing.
    """
    src = (ROOT / "dashboard" / "shipping.py").read_text()
    assert "diameter_mm" in src and "height_mm" in src
    products = json.loads(PRODUCTS.read_text())["products"]
    nac = products["nacetyl-cysteine"]
    assert nac["bottle_type"] == "120 caps"
    assert "Powder" in nac["panel_note"]


def test_nothing_about_identity_moved(products):
    """A slug or name change here would break QuickBooks or an existing link."""
    assert products[PURE]["name"] == "TMG"
    assert products[PURE]["price_cents"] == 3997
    assert products[PURE]["url"].endswith("500-tmg")
    assert products[BLEND]["name"] == "TMG Syntropy Powder"
    assert products[RETIRED]["name"] == "TMG Powder (Trimethylglycine)"


def test_the_file_still_parses_and_kept_its_formatting():
    """A reformat here buries a 13 line change in a 44,000 line diff."""
    raw = PRODUCTS.read_text()
    assert raw.endswith("}\n")
    assert '"aliases": ["Sleep Synergy"],' in raw, (
        "the one hand-compacted array must survive a round trip through json.dumps"
    )
