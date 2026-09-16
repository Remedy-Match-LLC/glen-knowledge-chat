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

# THREE RENAMES ARE HELD, and the reason is money rather than taste.
#
# author_invoice computes a month's supply as ceil(doses/day * 30 / doses_per_bottle) and
# reads doses_per_bottle from fmp_snap_products WHERE lower(product_name)=lower(?), exact,
# with no slug fallback. No row means None, and bottles_needed falls back to qty 1.
#
# The name it looks up comes off the report layer, written by resolve_remedy_name, whose
# candidate pool is FMP product names PLUS catalog names. So a name that exists only in the
# catalog can reach a layer and then find no FileMaker row, silently invoicing one bottle
# instead of three.
#
# Glen's three new Sublingual Powder names deliberately differ from FileMaker, which still
# holds "Adrenal Syntropy Powder", "Endocrine Restore Powder" and "Sublingual B12 Powder".
# They ship once those records are renamed. The retirements and was-prices are NOT held:
# only the names carry this risk.
#
# The other five renames are safe for a good reason. The pairs rule moves the TWIN's name
# onto the survivor, and the twin's name is already the FileMaker one. That is an argument
# for the rule, not against it.
HELD_PENDING_FILEMAKER_RENAME = {
    "adrenal-syntropy": "Adrenal Syntropy Sublingual Powder",
    "endocrine-restore": "Endocrine Restore Sublingual Powder",
    "sublingual-b12": "B12 Sublingual Powder",
}

PAIRS = [
    ("adrenal-syntropy", "adrenal-syntropy-powder", "Adrenal Syntropy"),
    ("endocrine-restore", "endocrine-restore-powder", "Endocrine Restore"),
    ("sublingual-b12", "sublingual-b12-powder", "Sublingual B12"),
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


# --- the held renames ------------------------------------------------------------------

def test_the_three_sublingual_names_are_not_shipped_yet(products):
    """Shipping these before FileMaker is renamed under-invoices a month's supply."""
    for slug, intended in HELD_PENDING_FILEMAKER_RENAME.items():
        assert products[slug]["name"] != intended, (
            f"{slug} carries a catalog-only name; author_invoice would bill one bottle"
        )


def test_every_shipped_name_exists_in_the_filemaker_export():
    """The invariant behind the hold, checked against the export rather than asserted.

    Skips when the export is not in this checkout, and says so rather than passing quietly.
    """
    import csv
    import os
    # The export lives in the VAULT, not this repo, so this guard runs on Glen's Mac and
    # skips in CI. It is the only check that can see FileMaker's side, so it is worth
    # having even though it cannot run everywhere. The hold itself is pinned by
    # test_the_three_sublingual_names_are_not_shipped_yet, which runs everywhere.
    export = pathlib.Path(os.path.expanduser(
        "~/AI-Training/00 System/fmp-extracts/bom-weekly/products.csv"))
    if not export.exists():
        pytest.skip("FileMaker export lives in the vault; not present in CI")
    rows = list(csv.DictReader(export.open(encoding="utf-8-sig")))
    col = next(c for c in rows[0] if c.strip().lower() in ("product_name", "product name", "name"))
    fmp = {(r.get(col) or "").strip().lower() for r in rows}
    products = json.loads(PRODUCTS.read_text())["products"]
    # WHICH SIDE IS WRONG, for each of these, so nobody "fixes" the correct one:
    #   Neem  -- Glen ruled 2026-09-16 "roll-on is correct". The CATALOG is right and
    #            FileMaker's "Neem Oil Roll On" is the record to rename. Do not drop the
    #            hyphen from the catalog to make the lookup pass.
    #   The three Sublinguals -- FileMaker is to be renamed to Glen's new names, and the
    #            catalog half is held here until it is.
    # In every case the fix is on the FileMaker side. Until it lands these four under-
    # invoice a month's supply as one bottle.
    #
    # BROKEN BEFORE THIS BATCH, and not made worse by it. Measured against origin/main:
    # each of these survivors already carried a name FileMaker does not have, so a layer
    # carrying the catalog name already bills one bottle today. FileMaker calls them
    # "Adrenal Syntropy Powder", "Endocrine Restore Powder", "Sublingual B12 Powder" and
    # "Neem Oil Roll On".
    #
    # This is the correction to the brief that prompted the hold. It reported the three
    # Sublingual names as breaks the rename CAUSES. They are not: neither the old name nor
    # the new one matches. The rename is half of the fix, and lands once Glen renames the
    # FileMaker records. Until then the catalog keeps the name it has, so the two halves
    # arrive together rather than leaving a window where only one has moved.
    BROKEN_BEFORE_THIS_BATCH = {
        "adrenal syntropy", "endocrine restore", "sublingual b12", "neem oil roll-on",
    }
    missing = [products[k]["name"] for k, _, _ in PAIRS
               if products[k]["name"].lower() not in fmp
               and products[k]["name"].lower() not in BROKEN_BEFORE_THIS_BATCH]
    assert not missing, (
        "these shipped catalog names have no FileMaker product and would bill one "
        f"bottle: {missing}"
    )
