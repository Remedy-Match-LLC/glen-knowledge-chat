"""The Scar Support Program packs the live MSM Syntropy listing.

It named `msm-syntropy-powder`, the retired record (superseded by `msm-syntropy`), so
packing expanded the program to an inactive record. Glen "yes", platform tab, 2026-11-04,
on money's request: repoint that one component to `msm-syntropy`. Both are $70, so the
program's price rule (components less 10%) and its $252 price are unchanged.
"""
import json
import pathlib

from dashboard import shipping

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTS = json.loads((ROOT / "data" / "products.json").read_text())["products"]
PROGRAM = PRODUCTS["scar-reduction-program"]


def test_the_program_names_the_live_msm():
    slugs = [c["slug"] for c in PROGRAM["bundle_component_slugs"]]
    assert slugs == ["scar-silk", "scar-solve", "vitamin-e-spectrum", "msm-syntropy"]
    assert not PRODUCTS["msm-syntropy"].get("inactive")


def test_packing_expands_to_four_active_records():
    catalog = [dict(p, slug=s) for s, p in PRODUCTS.items()]
    parts = shipping.bundle_component_products(dict(PROGRAM, slug="scar-reduction-program"),
                                               catalog)
    assert [p["slug"] for p in parts] == ["scar-silk", "scar-solve", "vitamin-e-spectrum",
                                          "msm-syntropy"]
    assert not any(p.get("inactive") for p in parts)


def test_the_price_is_unchanged():
    assert PROGRAM["price_cents"] == 25200
    assert PROGRAM["price_rule"] == "components_less_10pct"
    parts = sum(PRODUCTS[c["slug"]]["price_cents"] * c.get("qty", 1)
                for c in PROGRAM["bundle_component_slugs"])
    assert parts == 28000
