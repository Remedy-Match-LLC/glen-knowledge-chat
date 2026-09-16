"""Eleven duplicate pairs collapse to one entry each, plus Transform.

Glen ruled the batch on 2026-09-16: "pairs rule stands" and "move to the Transform record".
Spec: production/02 Products/catalog-duplicate-pairs/pairs-2026-09-16.html

The rule: keep the store entry, move the twin's was-price and better name onto it, retire
the twin with `inactive: true` plus `superseded_by`. Never delete, because order history,
storefront links and Atlas ids still reference the retired slugs.

FOUR OF THE ELEVEN ARE NOT UNIFORM, and each is pinned below so nobody pattern-matches the
list later:

  ACES inverts. The kept slug is the hyphenated one. That pair was ALREADY retired before
  this batch, and is left untouched.

  Relax's survivor is itself already retired, superseded by stress-release. Renaming a dead
  record would be noise and its name is never shown, so it keeps its own. The resolver
  chains, so a Relax Powder buyer lands on Stress Release. Glen confirmed that destination
  on 2026-09-16 when asked.

  MSM's survivor was named "MSM Synergy". This merge fixes that spelling.

  Adrenal is the only pair with paid orders on BOTH sides, so its redirect is the one that
  most needs to be right.

Transform is NOT a merge. Glen: "Transform will be discontinued in its present form when
the current stock (capsules) runs out. Transform powder already out." So the powder retires
now and the capsule stays live, repointed to the FileMaker product that actually holds the
capsule formula.
"""
import json
import pathlib

import pytest

from dashboard import products as pm

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRODUCTS = ROOT / "data" / "products.json"

PAIRS = [
    ("adrenal-syntropy", "adrenal-syntropy-powder", "Adrenal Syntropy Sublingual Powder"),
    ("endocrine-restore", "endocrine-restore-powder", "Endocrine Restore Sublingual Powder"),
    ("sublingual-b12", "sublingual-b12-powder", "B12 Sublingual Powder"),
    ("flow-ease", "flow-ease-powder", "Flow Ease Powder"),
    ("msm-syntropy", "msm-syntropy-powder", "MSM Syntropy Powder"),
    ("seaaminos", "seaamino-powder", "SeaAmino Powder"),
    ("crucifer-complex", "crucifer-complex-powder", "Crucifer Complex Powder"),
    ("hydrolyzed-collagen", "hydrolyzed-collagen-powder", "Hydrolyzed Collagen Powder"),
    ("neem-oil-rollon", "neem-oil-roll-on", "Neem Oil Roll-On"),
]


@pytest.fixture(scope="module")
def products():
    return json.loads(PRODUCTS.read_text())["products"]


def _sellable(products, slug):
    """_get_product's logic: follow the pointer, then refuse anything inactive."""
    s = pm.superseded_slug(slug, products)
    p = products.get(s)
    if not p or p.get("inactive"):
        return None
    out = dict(p)
    out["slug"] = s
    return out


@pytest.mark.parametrize("keep,retire,name", PAIRS)
def test_the_twin_is_retired_and_points_at_its_survivor(products, keep, retire, name):
    r = products[retire]
    assert r["inactive"] is True
    assert r["superseded_by"] == keep


@pytest.mark.parametrize("keep,retire,name", PAIRS)
def test_the_survivor_takes_the_better_name(products, keep, retire, name):
    assert products[keep]["name"] == name


@pytest.mark.parametrize("keep,retire,name", PAIRS)
def test_the_survivor_stays_sellable_and_keeps_its_own_price(products, keep, retire, name):
    """Keeping the store entry means the store entry's price is what a buyer pays.

    Three twins were priced at $40.00 against their survivor's $39.97. The survivor wins,
    so those buyers see a three cent change.
    """
    live = _sellable(products, keep)
    assert live is not None and live["slug"] == keep
    assert live["price_cents"] == products[keep]["price_cents"]


@pytest.mark.parametrize("keep,retire,name", PAIRS)
def test_a_dead_slug_still_prices(products, keep, retire, name):
    """An old link or an order line must resolve to something sellable, not to None."""
    line = _sellable(products, retire)
    assert line is not None, f"{retire} resolves to nothing sellable"
    assert line["slug"] == keep


@pytest.mark.parametrize("keep,retire,name", PAIRS)
def test_the_survivor_carries_the_was_price(products, keep, retire, name):
    """`regular_cents` is the compare-at figure, and it lived only on the twins."""
    if "regular_cents" in products[retire]:
        assert products[keep].get("regular_cents") == products[retire]["regular_cents"]


# --- the four that are not uniform -----------------------------------------------------

def test_aces_inverts_and_was_already_done(products):
    """The kept slug is the hyphenated one, and this pair predates the batch."""
    assert products["aces-eyedrops"]["inactive"] is True
    assert products["aces-eyedrops"]["superseded_by"] == "aces-eye-drops"
    assert not products["aces-eye-drops"].get("inactive")
    assert products["aces-eye-drops"]["name"] == "ACES Eye Drops"


def test_relax_powder_lands_on_stress_release(products):
    """Its survivor is itself retired, so the resolver chains one more hop.

    Glen confirmed the destination on 2026-09-16. Without the chain a Relax Powder buyer
    would resolve to nothing sellable, which is why this is asserted rather than assumed.
    """
    assert products["relax-powder"]["inactive"] is True
    assert products["relax-powder"]["superseded_by"] == "relax"
    assert products["relax"]["inactive"] is True, "relax was already retired before this"
    line = _sellable(products, "relax-powder")
    assert line is not None and line["slug"] == "stress-release"


def test_relax_keeps_its_own_name(products):
    """Renaming a record the resolver chains straight past would be noise."""
    assert products["relax"]["name"] == "Relax"


def test_the_relax_twins_leading_asterisk_is_not_carried_anywhere(products):
    assert products["relax-powder"]["name"].startswith("*"), "the twin's own name is unchanged"
    assert not products["relax"]["name"].startswith("*")


def test_msm_spelling_is_fixed_by_this_merge(products):
    """The survivor was "MSM Synergy", so MSM needs no place in the Synergy rename batch."""
    assert products["msm-syntropy"]["name"] == "MSM Syntropy Powder"
    assert "Synergy" not in products["msm-syntropy"]["name"]


def test_adrenal_resolves_because_it_has_paid_orders_on_both_sides(products):
    line = _sellable(products, "adrenal-syntropy-powder")
    assert line is not None and line["slug"] == "adrenal-syntropy"


# --- Transform, which is not a merge ---------------------------------------------------

def test_transform_powder_retires_now(products):
    """Glen: "Transform powder already out"."""
    assert products["transform-powder"]["inactive"] is True
    assert products["transform-powder"]["superseded_by"] == "transform"


def test_transform_stays_live_until_the_capsule_stock_runs_out(products):
    assert not products["transform"].get("inactive")
    assert _sellable(products, "transform") is not None


def test_transform_points_at_the_capsule_record_not_the_powder_one(products):
    """Two FileMaker products share almost the same name. Told apart by CONTENTS.

    id_pk 349 "Transform Powder" holds no ingredients and is the form already gone.
    id_pk 881 "Transform" holds five including a Plantcaps capsule. Both catalog entries
    pointed at 349.
    """
    assert products["transform"]["fmp_id"] == "881"
    assert products["transform-powder"].get("fmp_id") == "349", (
        "the retired powder keeps its own pointer; only the survivor was repointed"
    )


# --- the whole file --------------------------------------------------------------------

def test_no_retirement_anywhere_resolves_to_nothing_sellable(products):
    """The one invariant that matters. A dead end strands an order line."""
    broken = []
    for slug, p in products.items():
        if isinstance(p, dict) and p.get("superseded_by"):
            if _sellable(products, slug) is None:
                broken.append(slug)
    assert not broken, f"these retirements strand a buyer: {broken}"


def test_bioavailability_blend_was_left_alone(products):
    """Out of the batch: on the do-not-recommend list and never sold on either side."""
    for slug in products:
        if "bioavailability" in slug:
            assert not products[slug].get("superseded_by"), f"{slug} should not be in this batch"


def test_the_file_kept_its_formatting():
    raw = PRODUCTS.read_text()
    assert raw.endswith("}\n")
    assert '"aliases": ["Sleep Synergy"],' in raw
