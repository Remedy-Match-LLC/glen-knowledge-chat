"""Three catalog entries for one product, two of them publishing a formula that is not it.

Glen ruled it on 2026-09-20: "Quercetin Dihydrate also would be a pure powder".

FileMaker 547 types this product "Pure Powders", 60 g, active, and the catalog's own third
entry carries fmp_id 547. The two storefront entries published Wheat Grass Juice 300 mg,
Quercetin Dihydrate 200 mg and Zeolite 100 mg, which is row FOR000060, whose first name is
"Zeolite Cleanse". The catalog matched its third name line and took the whole formula.

Same mechanism as msm-powder, at milligram doses instead of gram.

Spec: production/05 Formulations/quercetin-dihydrate/2026-09-20/catalog-merge-spec.html
"""
import io
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "data" / "products.json"
SURVIVOR = "quercetin-dihydrate"
RETIRED = ("quercetin-dihydrate-powder", "quercetin-dihydrate-powder-60-grams")
ZEOLITE_CLEANSE = {"Wheat Grass Juice", "Zeolite"}


def _catalog():
    return json.loads(io.open(CATALOG, encoding="utf-8").read())["products"]


def test_the_survivor_publishes_only_quercetin():
    p = _catalog()[SURVIVOR]
    assert [i["name"] for i in p["ingredients"]] == ["Quercetin Dihydrate"]
    assert p["ingredients_source"] == "fmp-547-2026-09-20"


def test_the_survivor_states_no_dose_because_none_is_sourced():
    """FileMaker 547 gives a 60 g bottle and a 1 scoop dose and never a scoop weight.
    Production's own spec put 500 mg in a table and then said in prose not to print it,
    because the figure is theirs and unsourced. The prose wins."""
    assert _catalog()[SURVIVOR]["ingredients"][0]["dose"] == ""


def test_the_survivor_carries_filemakers_own_directions():
    """Three times daily here, twice for MSM. FileMaker differs per product, so this is
    read from zc_dosage_display on 547 rather than copied across."""
    assert (_catalog()[SURVIVOR]["directions"]
            == "Take 1 scoop 3 times daily in a drink, or as guided.")


def test_the_survivor_gains_filemakers_identity():
    assert _catalog()[SURVIVOR]["fmp_id"] == "547"


def test_the_survivor_keeps_slug_name_price_and_link():
    """The name is the QuickBooks invoice identity and the url is on the storefront, so
    a merge that changed either would break invoicing or an existing link."""
    p = _catalog()[SURVIVOR]
    assert p["name"] == "Quercetin Dihydrate"
    assert p["price_cents"] == 3997
    assert p["url"].endswith("/begin/product/quercetin-dihydrate")
    assert p.get("inactive") is not True, "the survivor must not be retired"
    assert "superseded_by" not in p


def test_both_duplicates_retire_to_the_survivor():
    """Retired, never deleted. _get_product routes a retired slug through _superseded, so
    order history and existing storefront links keep resolving."""
    c = _catalog()
    for slug in RETIRED:
        assert c[slug].get("inactive") is True, slug
        assert c[slug].get("superseded_by") == SURVIVOR, slug


def test_no_sellable_page_still_publishes_the_zeolite_cleanse_formula():
    """The class-level guard. A retired row keeps its old ingredients as history, which is
    fine because nothing serves them. A row anyone can still buy must not."""
    bad = []
    for slug, p in _catalog().items():
        if p.get("inactive") is True or p.get("superseded_by"):
            continue
        names = {i.get("name") for i in (p.get("ingredients") or [])}
        if "Quercetin Dihydrate" in names and ZEOLITE_CLEANSE & names:
            bad.append(slug)
    assert not bad, "sellable pages publishing Zeolite Cleanse as quercetin: " + ", ".join(bad)
