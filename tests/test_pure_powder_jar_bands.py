"""Eleven pure powders get the jar their cost per gram puts them in.

Glen, 2026-09-17: "Jar size for pure powders varies primarily with cost per gram." The
packer's bands, relayed the same day:

    under $0.15/g   ->  '120 caps', the WholOmega jar, 72 x 100 mm, about 100 g fill
    $0.15 to $0.30  ->  '30 g', the small bottle, 65 x 75 mm
    over $0.30      ->  '30 g' as well, since Glen added "at higher costs, smaller gram
                        quantities can be put in the 30 g size bottle"

The jar is rated by the JAR, never by the fill, so a smaller fill in the same bottle needs
no new packing type. That is why the dear powders are not blocked on a bottle prod lacks.

WHY THIS MATTERS AT ALL. One untyped line drops the WHOLE cart to the coarse quantity rule,
so an unpackaged powder overcharges shipping on everything bought with it. See
reference_no_bottle_type_breaks_the_whole_cart.

TWO SLUGS PRODUCTION NAMED WRONGLY, and this is the point of pinning them here. They sent
'ursolic-acid-50' and 'magnesium-acetyl-taurate'. Neither exists. The live slugs are
'ursolic-acid' and 'magnesium-acetyltaurate'. Setting the sent spelling would have created
two new products rather than typing the real ones. Note that 'magnesium-taurate' is a
SEPARATE live product going to a different jar, so the two must never be collapsed.

WHAT IS DELIBERATELY NOT HERE, and why each is out.

'quercetin-dihydrate' and 'quercetin-dihydrate-powder' are BOTH untyped. They carry
identical three-item lists, Wheat Grass Juice + Quercetin Dihydrate + Zeolite, so each is a
blend priced from one raw material's cost per gram, and that is not a cost the band can
read. They are also plainly one product under two slugs. Neither has an fmp_id, which is
why the duplicate-pair scan that found thirteen pairs could not see this one. Typing them
would entrench the duplicate.

'lutein' (10 ingredients), 'lycopene' (11) and 'quercetin-dihydrate' (3) were withdrawn
from the batch for the same reason and never reached the catalog.

COUNT THE AUDIT'S REACH, not just its hits. Of 39 products proposed for a jar, only TEN
carry any ingredient list, and the formula check found five formulas among those ten. It
could say nothing about the other 29. They are unchecked, not clean, and a later reader
should not take "the formulas were removed" as meaning the rest were examined.

S-ACETYL GLUTATHIONE AND L-CARNOSINE ARE OUT, AND THIS IS THE INTERESTING ONE. I first
kept glutathione despite its 14 ingredients, because its description states "30 grams of
powder in a Belgian violet glass bottle". A stated fill does beat a cost band. But the jar
was still wrong, and another product's description says so:

  lcarnosine: "97 for 30g in 50 mL violet glass bottle (bottle retail value: $19 from
  Infinity Jars)"

So the violet bottle is 50 mL holding 30 g. The `30 g` packing type is a 100 ml cosmetic
jar at 65 x 75 mm, twice the volume. Same fill, different container, and at 65 x 75 it
sits above the threshold money measured while a 50 mL jar may not. Both products wait for
a 50 mL violet type with real measured dimensions rather than take a jar known to be too
big.

THE LESSON, because it cost two wrong assignments: a stated FILL and a stated JAR are
different facts. "30 grams of powder" fixes the fill and says nothing about the container.
Only lcarnosine's sentence names the container, and it took a second product to find it.
"""
import json

import pytest

from dashboard.shipping import PROD_BOTTLE_NAMES

# slug -> (jar, cost per gram used, the quote that cost came from)
BANDS = {
    "humic-acid":                    ("120 caps", None,   "packer ruling, relayed"),
    "polyphenols-camellia-sinensis": ("120 caps", None,   "packer ruling, relayed"),
    "serrapeptase":                  ("120 caps", None,   "packer ruling, relayed"),
    "salvianolic-acid":              ("30 g",     None,   "packer ruling, relayed"),
    "transresveratrol":              ("30 g",     None,   "packer ruling, relayed"),
    "honokiol":                      ("30 g",     None,   "packer ruling, relayed"),
    "apigenin":                      ("30 g",     None,   "packer ruling, relayed"),
    "ursolic-acid":                  ("30 g",     0.2050, "Xi'an Plant Bio, 1000 g"),
    "magnesium-acetyltaurate":       ("30 g",     0.3500, "1000 g, in stock"),
}


@pytest.fixture(scope="module")
def products():
    return json.load(open("data/products.json"))["products"]


@pytest.mark.parametrize("slug,jar", [(s, v[0]) for s, v in BANDS.items()])
def test_each_powder_carries_its_jar(products, slug, jar):
    assert slug in products, f"{slug} is not a catalog slug, so nothing was typed"
    assert products[slug].get("bottle_type") == jar


@pytest.mark.parametrize("slug", BANDS)
def test_none_of_them_was_retired_under_us(products, slug):
    """Typing a retired product is wasted, and hides that the live twin is still bare."""
    p = products[slug]
    assert not p.get("inactive"), f"{slug} is inactive"
    assert not p.get("superseded_by"), f"{slug} points at {p.get('superseded_by')}"


@pytest.mark.parametrize("slug,jar", [(s, v[0]) for s, v in BANDS.items()])
def test_every_jar_is_a_name_prod_knows(slug, jar):
    """A bottle_type prod's library lacks is INERT: pick_boxes raises, the cart falls back
    to the qty rule, and the test that seeded its own vocabulary stays green."""
    assert jar in PROD_BOTTLE_NAMES


@pytest.mark.parametrize("slug,jar,cost", [
    (s, v[0], v[1]) for s, v in BANDS.items() if v[1] is not None])
def test_the_measured_costs_land_in_the_band_they_were_assigned(slug, jar, cost):
    """Only the three with a real quote. The other eight rest on the relayed ruling, and
    saying so is the point: a band with no cost behind it must not look measured."""
    expected = "120 caps" if cost < 0.15 else "30 g"
    assert expected == jar, f"{slug} at ${cost}/g belongs in {expected}, not {jar}"


def test_the_two_misspelled_slugs_were_not_created(products):
    """The regression this file exists for. Creating them would mint phantom products."""
    for wrong in ("ursolic-acid-50", "magnesium-acetyl-taurate"):
        assert wrong not in products, f"{wrong} was created; it is a typo, not a product"


def test_magnesium_taurate_is_a_different_product_and_is_untouched(products):
    """Two magnesiums, two jars. Collapsing them would mis-pack one of them."""
    assert "magnesium-taurate" in products
    assert products["magnesium-taurate"] is not products["magnesium-acetyltaurate"]
    assert products["magnesium-taurate"].get("bottle_type") is None, (
        "magnesium-taurate was not in this batch and must stay as it was"
    )


@pytest.mark.parametrize("slug", [
    "quercetin-dihydrate", "quercetin-dihydrate-powder", "lutein", "lycopene"])
def test_the_withdrawn_formulas_stay_untyped(products, slug):
    """Each is a multi-ingredient blend that was priced from ONE raw material's cost per
    gram. The band cannot read that, so a value here would look measured and be invented.
    Re-adding any of them needs a fill or a jar, not a cost."""
    assert slug in products
    assert products[slug].get("bottle_type") is None, (
        f"{slug} is a formula and its jar cannot come from a cost band"
    )


def test_the_quercetin_pair_has_been_merged(products):
    """INVERTED on 2026-09-20, deliberately, which is what the old version asked for.

    This used to assert the pair was still two slugs carrying one formula and no fmp_id,
    and it said that a later merge should make it fail and be deleted. Glen ruled the
    merge that day, so it now asserts the other side: one survivor, one retired twin.
    The full merge is covered by tests/test_quercetin_catalog_merge.py."""
    a, b = products["quercetin-dihydrate"], products["quercetin-dihydrate-powder"]
    assert a.get("fmp_id") == "547"
    assert b.get("superseded_by") == "quercetin-dihydrate" and b.get("inactive") is True


@pytest.mark.parametrize("slug", ["sacetyl-glutathione", "lcarnosine"])
def test_the_violet_bottle_products_wait_for_their_own_type(products, slug):
    """Both state a 30 g fill and both ship in the 50 mL violet glass bottle, which is NOT
    the 100 ml cosmetic jar that '30 g' names. Typing them '30 g' rates a container twice
    the real volume, and at 65 x 75 mm that crosses a shipping threshold a 50 mL jar may
    not. They stay bare until the 50 mL type exists with MEASURED dimensions."""
    assert products[slug].get("bottle_type") is None


def test_the_evidence_for_the_violet_bottle_size_is_still_there(products):
    """The whole decision above rests on one sentence in a DIFFERENT product. Pin it, or a
    description rewrite silently removes the reason and the next session re-adds the jar."""
    d = products["lcarnosine"].get("description") or ""
    assert "50 mL violet glass bottle" in d
    assert "30g" in d, "the fill and the container must both stay stated"
