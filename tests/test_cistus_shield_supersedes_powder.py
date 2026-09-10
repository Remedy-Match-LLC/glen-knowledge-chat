"""Cistus Syntropy Powder is retired; Cistus Shield capsules replace it.

Glen, 2026-09-10: "Cistus Syntropy powder is replaced by Cistus Shield capsules."
Spec: communication/plans/2026-09-10-cistus-shield-supersedes-powder.md

FMP already had the powder inactive. `data/products.json` did not, so every consumer
still sold it. Four things had to move together:

  1. The powder record goes `inactive` AND gains `superseded_by`. One without the other
     is the trap: `resolve_remedy_slug` matches a spoken name against `product["name"]`
     and never checks `inactive`, so a bare "Cistus" would keep landing on a record that
     `_get_product` then refuses to sell, and the failure would be silent.
  2. Cistus Shield gains its eight-ingredient array. An empty array is what hid it from
     `/api/products`, the ingredient-enriched subset the console product list reads.
  3. Three spoken names resolve to nothing in the catalog and need aliases, including
     Glen's own misspelling "Cystus Shield", which he used on 2026-09-10.
  4. The publish path stores the slug it resolves. Per
     `test_superseded_write_boundary.py`, the redirect belongs at the WRITE boundary, so
     no new dead slug is stored. `biofield_portal_publish` was not covered by that rule.
"""
import json

import pytest

from dashboard import products as products_mod
from dashboard import biofield_portal_publish as bpp

POWDER = "cistus-syntropy-immunitea"
SHIELD = "cistus-shield"


@pytest.fixture(scope="module")
def catalog():
    """The repo file itself, read explicitly — never via DATA_DIR, which other tests
    in this suite repoint at a two-product fixture."""
    return json.load(open("data/products.json"))["products"]


# ── change 1: the powder is retired and points at the capsules ──
def test_the_powder_is_inactive_and_points_at_the_capsules(catalog):
    p = catalog[POWDER]
    assert p.get("inactive") is True
    assert p.get("superseded_by") == SHIELD


def test_the_powder_redirects_to_the_capsules(catalog):
    assert products_mod.superseded_slug(POWDER, catalog) == SHIELD


def test_the_capsules_are_live_and_terminal(catalog):
    """The survivor must not itself be retired, or the redirect lands nowhere sellable."""
    assert not catalog[SHIELD].get("inactive")
    assert products_mod.superseded_slug(SHIELD, catalog) == SHIELD


# ── change 2: the capsules carry their ingredients ──
EXPECTED_INGREDIENTS = [
    ("PEA (Palmitoylethanolamide) ultramicronized", "100 mg"),
    ("Polyphenols 65% (Cistus incanus)", "100 mg"),
    ("Cryptolepis 20:1 (Cryptolepis sanguinolenta)", "83 mg"),
    ("Oxindole Alkaloids 5% (Uncaria tomentosa)", "58 mg"),
    ("Trans-Resveratrol 98% (Polygonum cuspidatum)", "33 mg"),
    ("Glycyrrhizic Acids 26% (Glycyrrhiza glabra)", "32 mg"),
    ("Baikal Skullcap Baicalein 98% (Scutellaria baicalensis)", "17 mg"),
    ("Andrographolide 98% (Andrographis paniculata)", "10 mg"),
]


def test_the_capsules_carry_the_eight_actives_in_order(catalog):
    got = [(i["name"], i["dose"]) for i in catalog[SHIELD]["ingredients"]]
    assert got == EXPECTED_INGREDIENTS


def test_the_third_largest_active_is_named(catalog):
    """`product_ingredients` carries a blank raw_name for fmp_raw_id 5609, so anyone
    publishing from that table ships an empty 83 mg line on a label-facing field. The
    catalog must not repeat it."""
    line = catalog[SHIELD]["ingredients"][2]
    assert "Cryptolepis sanguinolenta" in line["name"]
    assert line["dose"] == "83 mg"


def test_the_actives_total_433_mg(catalog):
    total = sum(int(i["dose"].split()[0]) for i in catalog[SHIELD]["ingredients"])
    assert total == 433


def test_the_capsules_carry_a_bottle_size(catalog):
    """From the FMP BOM for product 1193: four material lines at qty 30, including a
    100 mL wide-neck bottle described as 'for 30 caps'. `fmp_products` has no row."""
    assert catalog[SHIELD].get("bottle_type") == "30 Caps"


# ── change 3: the three unresolvable spoken names ──
@pytest.mark.parametrize("spoken", [
    "Cistus Syntropy",
    "Cistus Syntropy Powder",
    "Cystus Shield",          # Glen's own misspelling, used 2026-09-10
])
def test_a_name_that_resolved_to_nothing_now_reaches_the_capsules(spoken, catalog):
    assert bpp.resolve_remedy_slug(spoken, catalog) == SHIELD


@pytest.mark.parametrize("spoken", ["Cistus Shield", "Cistus Synergy"])
def test_a_name_that_already_resolved_still_reaches_the_capsules(spoken, catalog):
    """Pins behaviour that must not change. 'Cistus Synergy' is the powder's catalog
    name and reached it directly; it must now carry forward to the survivor."""
    assert bpp.resolve_remedy_slug(spoken, catalog) == SHIELD


def test_an_unrelated_name_is_untouched(catalog):
    """The alias table must not become a catch-all. A real, different product still
    resolves to itself."""
    assert bpp.resolve_remedy_slug("Binder Complex", catalog) == "binder-complex"


def test_a_genuinely_unknown_name_still_resolves_to_nothing(catalog):
    assert bpp.resolve_remedy_slug("Not A Real Remedy At All", catalog) is None


# ── change 4: the publish path stores the survivor, not the dead slug ──
def test_the_publish_resolver_never_returns_a_retired_slug(catalog):
    """`resolve_remedy_slug` feeds `reorder_items`, which is PERSISTED portal content.
    Storing the dead slug leaves the portal carrying a slug `_get_product` only saves by
    redirecting on every later read, and makes a scan naming both the old and new name
    emit the same product twice under two slugs."""
    retired = [s for s, p in catalog.items() if p.get("inactive") and p.get("superseded_by")]
    assert retired, "fixture guard: the catalog must hold at least one retired record"
    for slug in retired:
        name = catalog[slug].get("name")
        if not name:
            continue
        got = bpp.resolve_remedy_slug(name, catalog)
        assert got != slug, f"{name!r} stored the retired slug {slug!r}"
        assert not (catalog.get(got) or {}).get("inactive"), \
            f"{name!r} resolved to {got!r}, which is itself retired"


def test_the_capsules_carry_glens_dosage_line(catalog):
    """Glen, 2026-09-10: "Build up to 2 capsules 3 times a day according to tolerance."
    He confirmed separately that "with food" stays. Same six a day as the line it
    replaces, different schedule."""
    assert catalog[SHIELD]["description"] == (
        "Build up to 2 capsules 3 times a day according to tolerance, with food.")
