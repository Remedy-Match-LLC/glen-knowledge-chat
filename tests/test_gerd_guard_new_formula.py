"""GERD Guard moves to its new seven-ingredient formula.

Glen, 2026-09-21, in the production tab: which formula is made, "new one"; when the listing
changes, when the first new batch is made, and "it is being made today"; formats, "List both
formats for sale." The 2023 24-ingredient formula retires, gentian and artemisinin with it.

Spec: production/05 Formulations/gerd-guard/2026-09-21/listing-spec.html. The ingredient
table comes from the capsule LABEL, which is the approved panel, so the listing matches what
is printed on the bottle.

The formula and its copy ship together, never the formula alone. A seven-ingredient panel
beside prose naming gentian is the contradiction msm-powder shipped with on 2026-09-20.
"""
import io
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "data" / "products.json"

SEVEN = [
    ("DGL Licorice (Glycyrrhiza glabra)", "190 mg"),
    ("Fennel (Foeniculum vulgare) 4:1", "90 mg"),
    ("Quercetin Dihydrate", "90 mg"),
    ("Gingerol 10% (Zingiber officinale)", "20 mg"),
    ("Coral Calcium", "100 mg"),
    ("Mogroside V 50% (Siraitia grosvenorii)", "8 mg"),
    ("Piperine (Piper nigrum)", "2 mg"),
]
RETIRED_2023 = ("gentian", "artemisinin", "vitamin b6", "p5p")
CAPSULE_DIRECTIONS = ("Take 1 capsule before each meal that could cause symptoms, "
                      "or even when symptoms begin.")
OVERVIEW = ("GERD Guard comes in two forms with the same formula and the same amount per dose. "
            "The capsules hold 30 doses. The powder holds 60 scoops in 30 g. Stir one level "
            "scoop into a little water and sip it slowly. Some people prefer the powder for its "
            "taste, and it needs no swallowing of capsules. Each batch is handcrafted in small "
            "runs in Hilo, Hawai'i.")


def _p():
    return json.loads(io.open(CATALOG, encoding="utf-8").read())["products"]["gerd-guard"]


def test_the_capsules_publish_exactly_the_seven_from_the_label():
    got = [(i["name"], i["dose"]) for i in _p()["ingredients"]]
    assert got == SEVEN


def test_no_2023_ingredient_survives():
    """Gentian and artemisinin were removed from the formula. Checked by substring across
    every line so a renamed variant ("Gentian Root") cannot slip through."""
    names = " ".join(i["name"].lower() for i in _p()["ingredients"])
    assert not [t for t in RETIRED_2023 if t in names]


def test_the_capsule_directions_match_the_printed_label():
    """Glen changed the directions on 2026-09-21 and the labels were re-rendered. The listing
    has to say what the bottle says."""
    assert _p()["directions"] == CAPSULE_DIRECTIONS


def test_the_overview_is_the_approved_copy():
    """`description` renders as the Overview section, which reports ai=pending on this page
    and so is served straight from this field. The old copy opened "The Importance of
    Balancing the Body & the Brain in GERD"."""
    assert _p()["description"] == OVERVIEW


def test_the_capsules_keep_their_identity():
    """Glen did not rule on the name or price today, so they stay. The name is the
    QuickBooks invoice identity and fmp_id 377 is the FileMaker join."""
    p = _p()
    assert p["name"] == "GERD Guard"
    assert p["price_cents"] == 6997
    assert p["fmp_id"] == "377"
    assert p["bottle_type"] == "30 Caps"
