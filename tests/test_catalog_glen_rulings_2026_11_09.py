"""Glen's 2026-11-09 catalogue rulings: eight new entries at the prices he set."""
import json
import sqlite3
from pathlib import Path

import pytest

from dashboard import shipping

from dashboard.shipping import PROD_BOTTLE_NAMES

US = {"name": "T", "state": "CA", "country": "US"}
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
    assert "aliases" not in PRODUCTS["capsule-filling-machine"]
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


MEDIUM_BOX = ("miron-violet-glass-water-bottle-1l",
              "capsule-filling-machine-100m", "capsule-filling-machine-120s")


def test_medium_box_items_name_the_box_not_a_typed_rate():
    # Glen, 2026-11-10: Miron is 11.75 x 3.5 in, and "It fits" a USPS Medium box.
    # The packer's Medium model is too short for it, so it ships flat at the Medium
    # rate. Naming the box lets a USPS price change carry through.
    for slug in MEDIUM_BOX:
        p = PRODUCTS[slug]
        assert p["bottle_type"] == "own-box", slug
        assert p["flat_shipping_boxes"] == ["M"], slug
        assert "flat_shipping_cents" not in p, slug


@pytest.fixture
def medium_rate(monkeypatch, tmp_path):
    dbp = str(tmp_path / "chat_log.db")
    with sqlite3.connect(dbp) as cx:
        shipping.init_shipping_schema(cx)
    monkeypatch.setattr(shipping, "_default_db_path", lambda: dbp)

    def set_medium(retail_cents):
        rid = shipping.propose_rate_update("M", retail_cents, "test", "2099-01-01", db_path=dbp)
        shipping.confirm_rate_update(rid, "test", db_path=dbp)
        return shipping.get_current_rates(db_path=dbp)["M"]["charged_cents"]
    return set_medium


@pytest.mark.parametrize("slug", ["capsule-filling-machine-100m", "capsule-filling-machine-120s"])
def test_capsule_machines_charge_todays_medium_rate_per_unit(medium_rate, slug):
    import app
    seeded = shipping.get_current_rates()["M"]["charged_cents"]
    assert seeded == 2300
    pc = app._price_cart([{"slug": slug, "qty": 1}], ship=US, channel="retail")
    assert pc["shipping_cents"] == seeded
    pc2 = app._price_cart([{"slug": slug, "qty": 2}], ship=US, channel="retail")
    assert pc2["shipping_cents"] == 2 * seeded, "per unit, no per-order cap"
    raised = medium_rate(2475)
    assert raised != seeded
    pc3 = app._price_cart([{"slug": slug, "qty": 1}], ship=US, channel="retail")
    assert pc3["shipping_cents"] == raised


MIRON = "miron-violet-glass-water-bottle-1l"


def _set_rate(size, retail_cents):
    rid = shipping.propose_rate_update(size, retail_cents, "test", "2099-01-01")
    shipping.confirm_rate_update(rid, "test")
    return shipping.get_current_rates()[size]["charged_cents"]


@pytest.mark.parametrize("qty,larges,mediums", [(1, 0, 1), (2, 1, 0), (3, 1, 1), (4, 2, 0)])
def test_miron_two_to_a_large_box_leftover_in_a_medium(medium_rate, qty, larges, mediums):
    # Glen, 2026-11-14: "two fit in one large box".
    import app
    assert PRODUCTS[MIRON]["pair_box"] == "L"
    rates = shipping.get_current_rates()
    want = larges * rates["L"]["charged_cents"] + mediums * rates["M"]["charged_cents"]
    pc = app._price_cart([{"slug": MIRON, "qty": qty}], ship=US, channel="retail")
    assert pc["shipping_cents"] == want


def test_miron_pairs_follow_live_rates_and_pool_across_lines(medium_rate):
    import app
    large = _set_rate("L", 4075)
    medium = medium_rate(2475)
    assert (large, medium) != (3300, 2300)
    pc = app._price_cart([{"slug": MIRON, "qty": 3}], ship=US, channel="retail")
    assert pc["shipping_cents"] == large + medium
    pc2 = app._price_cart([{"slug": MIRON, "qty": 1}, {"slug": MIRON, "qty": 1}],
                          ship=US, channel="retail")
    assert pc2["shipping_cents"] == large


def test_miron_beside_a_capsule_machine_adds_both(medium_rate):
    import app
    rates = shipping.get_current_rates()
    pc = app._price_cart([{"slug": MIRON, "qty": 2},
                          {"slug": "capsule-filling-machine-100m", "qty": 1}],
                         ship=US, channel="retail")
    assert pc["shipping_cents"] == rates["L"]["charged_cents"] + rates["M"]["charged_cents"]


def test_miron_is_still_us_only(medium_rate):
    import app
    with pytest.raises(app.CheckoutError):
        app._price_cart([{"slug": MIRON, "qty": 2}], ship={"name": "T", "country": "AU"},
                        channel="retail")


def test_unknown_pair_box_is_refused_not_free():
    import app
    with pytest.raises(app.CheckoutError):
        app._pair_box_cents({"name": "X", "pair_box": "XL", "flat_shipping_boxes": ["M"]}, 2)


def test_pair_box_without_a_single_box_refuses_the_odd_unit():
    import app
    p = {"name": "X", "pair_box": "L"}
    with pytest.raises(app.CheckoutError):
        app._pair_box_cents(p, 1)
    with pytest.raises(app.CheckoutError):
        app._pair_box_cents(p, 3)


def test_pair_box_zero_or_negative_units_cost_nothing(medium_rate):
    import app
    p = PRODUCTS[MIRON]
    assert app._pair_box_cents(p, 0) == 0
    assert app._pair_box_cents(p, -2) == 0
    assert app._pair_box_cents(p, -1) == 0


@pytest.mark.parametrize("name,slug", [
    ("Miracule Water", "miracule-water"),
    ("Miracule Water 4 L", "miracule-water"),
    ("Miracule Water (4 L)", "miracule-water"),
    ("Alkaline Microwater", "microwater-alkaline"),
    ("Alkaline Microwater (4 L)", "microwater-alkaline"),
    ("Bioenergetic Wellness Scanner (USB)", "bioenergetic-wellness-scanner-usb"),
    ("Bioenergetic Wellness Scanner (Bluetooth)", "bioenergetic-wellness-scanner-bluetooth"),
    # Unchanged: the $1,497 system keeps its own name. The bare scanner page was retired
    # to the USB page (Glen 2026-11-26: "just 2 pages: usb and bluetooth").
    ("Miracule Water System with Molecular Hydrogen", "miracule-water-system"),
    ("Bioenergetic Wellness Scanner", "bioenergetic-wellness-scanner-usb"),
    ("Capsule Filling Machine 100M", "capsule-filling-machine-100m"),
    ("Capsule Filling Machine (100M)", "capsule-filling-machine-100m"),
    ("Capsule Filling Machine 120S", "capsule-filling-machine-120s"),
    ("Capsule Filling Machine (120S)", "capsule-filling-machine-120s"),
    ("Capsule Filling Machine", "capsule-filling-machine"),
])
def test_resolver_sends_short_names_to_the_new_entries(name, slug):
    from dashboard.practitioner_portal import exact_name_to_slug, name_to_slug
    assert name_to_slug(name, PRODUCTS) == slug
    assert exact_name_to_slug(name, PRODUCTS) == slug


def test_miracule_water_never_prices_as_the_system():
    from dashboard.practitioner_portal import name_to_slug
    for name in ("Miracule Water", "miracule water", "Miracule Water 4 L"):
        slug = name_to_slug(name, PRODUCTS)
        assert PRODUCTS[slug]["price_cents"] == 300, (name, slug)


def test_bacterotox_carries_its_siblings_lookup_fields():
    p = PRODUCTS["bacterotox-homeopathic-complex-in-terrain-restore"]
    assert p["pinecone_title"] == p["name"]
    assert p["ingredients_source"] == "fmp_snap"
