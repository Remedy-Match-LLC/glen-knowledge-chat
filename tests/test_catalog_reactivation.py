# tests/test_catalog_reactivation.py
"""Five product slugs that 404'd, fixed two different ways.

Found while repointing the truly.vip shortlinks: seven product pages returned 404,
so I refused to redirect a shortlink at them (swapping a 503 for a 404 is not a
fix). Glen ruled on five of them, 2026-08-22:

  * Comfort and Endocrine Restore are CURRENT products -- they were simply
    mis-flagged `inactive: true` in the catalog. Un-flagged.
  * Dental Regen Powder -> Dental Powder
  * Relax -> Stress Release
  * Connective Tissue Support -> Comfort

The three retirements use `superseded_by`, which `_get_product` already follows, so
the old slug keeps resolving instead of 404ing -- old links, order history and
repertoire entries all survive (see reference_bundle_packing_and_superseded).

Ordering matters here: Connective Tissue Support points at Comfort, so Comfort has
to be active or the redirect lands on another 404.

Superseded on 2026-10-30. Glen, formulation tab: comfort and comfort-synovial-syntropy are
one product ("yes"). comfort-synovial-syntropy (FileMaker 586, the label's name) stays live;
comfort retires onto it, and Connective Tissue Support now points at it directly so it does
not rely on a chain. Spec: production/05 Formulations/_store-updates/2026-10-31-comfort-merge.md.
"""

import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _catalog():
    return json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))["products"]


# ---------------------------------------------------------------------------
# The two that were mis-flagged
# ---------------------------------------------------------------------------

CURRENT = ["comfort-synovial-syntropy", "endocrine-restore"]


@pytest.mark.parametrize("slug", CURRENT)
def test_current_products_are_not_flagged_inactive(slug):
    """Glen: these are current products. Inactive with no successor => 404."""
    assert _catalog()[slug].get("inactive") is not True


@pytest.mark.parametrize("slug", CURRENT)
def test_current_products_are_sellable(slug):
    p = _catalog()[slug]
    assert p.get("price_cents"), f"{slug} has no price"
    assert p.get("name")


# ---------------------------------------------------------------------------
# The three retirements
# ---------------------------------------------------------------------------

# Dental Regen Powder left this list on 2026-11-06. Glen: "The old Dental Regen Powder is
# not sold out yet. Keep it listed separately until we sell those 3 bottles." Retire it
# to dental-powder again once its shelf count reaches 0.
RETIRED = {
<<<<<<< HEAD
    # Glen, 2026-11-06: "Holy Grail has 2 variant names" and "Hydrolyzed Collagen has 2
    # variants". Each folds into the listing that holds the alias, QuickBooks item or copy.
    "holy-grail-full-spectrum-drops": "holy-grail-ormus",
    "hydrolyzed-collagen-powder-grass-fed-type-i-iii": "hydrolyzed-collagen",
    # Glen, 2026-11-06: "a duplication of Mold Spray (with Mold Spray Mould Spray)".
    "mold-spray-mould-spray": "mold-spray",
    "dental-regen-powder": "dental-powder",
=======
    # Glen, 2026-11-06: "Digestzymes Homeoenergetic Drops also shows as Digestzymes (same
    # product)". One listing, the HomeoEnergetic one.
    "digestzymes": "digestzymes-homeoenergetic-drops",
    # Glen, 2026-11-06: "yes", three more twins are the same products. Here the plain listing
    # holds the ingredients and approved copy, so the FileMaker HomeoEnergetic row points to it.
    "energessence-homeoenergetic-drops": "energessence",
    "natural-resource-a-z-homeoenergetic-drops": "natural-resource-az",
    "shark-essence-homeoenergetic-drops": "shark-essence",
>>>>>>> origin/main
    "relax": "stress-release",
    "connective-tissue-support": "comfort-synovial-syntropy",
    "comfort": "comfort-synovial-syntropy",
}


@pytest.mark.parametrize("old,new", sorted(RETIRED.items()))
def test_retired_slugs_point_at_their_successor(old, new):
    assert _catalog()[old].get("superseded_by") == new


@pytest.mark.parametrize("old,new", sorted(RETIRED.items()))
def test_every_successor_is_itself_live(old, new):
    """A redirect to another retired SKU is still a 404. Connective Tissue
    Support pointed at Comfort; since Comfort retired too, it points at the survivor."""
    succ = _catalog()[new]
    assert succ.get("inactive") is not True, f"{old} -> {new}, but {new} is inactive"
    assert succ.get("superseded_by") in (None, ""), f"{old} -> {new} -> chained again"


def test_no_successor_chain_loops():
    cat = _catalog()
    for slug, p in cat.items():
        seen, cur = {slug}, p.get("superseded_by")
        while cur:
            assert cur not in seen, f"superseded_by loop at {slug}"
            seen.add(cur)
            cur = (cat.get(cur) or {}).get("superseded_by")


# ---------------------------------------------------------------------------
# Resolution through the app's own accessor
# ---------------------------------------------------------------------------

def _app():
    import importlib, sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app not importable: {e}")


@pytest.mark.parametrize("slug", ["comfort", "endocrine-restore",
                                  "dental-regen-powder", "relax",
                                  "connective-tissue-support",
                                  "comfort-synovial-syntropy"])
def test_get_product_resolves_all_five(slug):
    """The end the client actually hits: /begin/product/<slug> 404s when
    _get_product returns None."""
    app = _app()
    p = app._get_product(slug)
    assert p is not None, f"{slug} still resolves to nothing -> 404"
    assert p.get("name")


def test_dental_regen_powder_is_listed_until_its_last_bottles_sell():
    """Glen, 2026-11-06: listed on its own until its 3 bottles sell."""
    p = _catalog()["dental-regen-powder"]
    assert p.get("inactive") is not True
    assert not p.get("superseded_by")
    assert p.get("price_cents") == 7000
