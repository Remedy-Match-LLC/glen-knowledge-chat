"""Glen's 2026-11-09 catalogue rulings: eight new entries at the prices he set."""
import json
from pathlib import Path

from dashboard.shipping import PROD_BOTTLE_NAMES

PRODUCTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "products.json").read_text())["products"]

EXPECTED = {
    "miron-violet-glass-water-bottle-1l": 4000,
    "microwater-alkaline": 300,
    "miracule-water": 300,
    "capsule-filling-machine-100m": 19700,
    "capsule-filling-machine-120s": 19700,
    "bioenergetic-wellness-scanner-usb": 20000,
    "bioenergetic-wellness-scanner-bluetooth": 10000,
    "bacterotox-homeopathic-complex-in-terrain-restore": 7000,
}


def test_new_entries_carry_glens_prices():
    for slug, cents in EXPECTED.items():
        assert PRODUCTS[slug]["price_cents"] == cents, slug


def test_new_entries_have_a_bottle_type_prod_knows():
    # A missing or unknown bottle_type drops the whole cart to the coarse shipping rule.
    for slug in EXPECTED:
        assert PRODUCTS[slug]["bottle_type"] in PROD_BOTTLE_NAMES, slug


def test_old_entries_kept_so_links_still_work():
    assert PRODUCTS["capsule-filling-machine"]["price_cents"] == 19700
    assert PRODUCTS["bioenergetic-wellness-scanner"]["price_cents"] == 10000
    assert PRODUCTS["biofield-analysis"]["price_cents"] == 30000


def test_new_fmp_ids_are_not_shared_with_any_other_entry():
    for slug in EXPECTED:
        fid = PRODUCTS[slug]["fmp_id"]
        others = [k for k, p in PRODUCTS.items() if k != slug and str(p.get("fmp_id")) == fid]
        assert not others, (slug, fid, others)


def test_resolver_sends_scanner_models_to_their_own_entries():
    from dashboard.practitioner_portal import name_to_slug
    assert name_to_slug("Bioenergetic Wellness Scanner USB", PRODUCTS) == "bioenergetic-wellness-scanner-usb"
    assert name_to_slug("Bioenergetic Wellness Scanner Bluetooth", PRODUCTS) == "bioenergetic-wellness-scanner-bluetooth"
    assert name_to_slug("Miracule Water (4 L)", PRODUCTS) == "miracule-water"


def test_bacterotox_has_directions_and_sibling_list_price():
    p = PRODUCTS["bacterotox-homeopathic-complex-in-terrain-restore"]
    assert p["regular_cents"] == 8000
    assert p["description"].endswith("or as guided.")


def test_waters_are_pickup_only():
    # Glen, 2026-11-09: "pickup only". Checkout enforcement of the flag is platform's.
    for slug in ("microwater-alkaline", "miracule-water"):
        assert PRODUCTS[slug].get("pickup_only") is True, slug
