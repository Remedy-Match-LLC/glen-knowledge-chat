"""OcuHeal+ Eye Drops: a new catalog entry BESIDE OcuHeal Eye Drops.

Glen, 2026-09-24, relayed by production: "beside". OcuHeal Eye Drops
(`ocuheal-eye-drops`, FileMaker 493) stays on sale unchanged. The new entry carries
FileMaker 1200's exact name, because the invoice matches FileMaker by exact name.
The only formula change is DMSO from 0.5% to 10%, taken from the Quintessential
Bioterrain Restore base. Spec:
production/05 Formulations/ocuheal-plus/2026-09-24/listing-spec.md
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(ROOT, "data", "products.json")
SLUG = "ocuheal-plus-eye-drops"

PANEL = [
    ("Quintessential Bioterrain Restore", "86.5%"),
    ("DMSO (Dimethylsulfoxide)", "10%"),
    ("MSM (Methylsulfonylmethane)", "1%"),
    ("N-Acetyl L-Carnosine", "1%"),   # Glen, 2026-09-25: back to 1%
    ("Forskolin (Coleus forskohlii)", "0.1%"),
    ("Puerarin (Pueraria lobata)", "0.1%"),
    ("Vitamin A (Retinol)", "0.1%"),
    ("Vitamin B2 (Riboflavin 5-Phosphate)", "0.1%"),
    ("Vitamin B6 (Pyridoxal 5-Phosphate)", "0.1%"),
    ("Vitamin C & Zinc (Zinc Ascorbate)", "0.1%"),
    ("Vitamin E Complex", "0.01%"),
    ("Lanosterol", "0.01%"),
    ("Safranal 3% (Crocus sativus)", "0.1%"),
    ("Cineraria (Cineraria maritima) (succus)", "0.1%"),
    ("Silver (Nano)", "0.0001%"),
    ("Serrapeptase (Serratia marcescens)", "5C"),
]
DIRECTIONS = "1 drop in each eye 2 times a day."


@pytest.fixture(scope="module")
def products():
    return json.load(open(PRODUCTS))["products"]


def test_the_entry_matches_filemaker_1200(products):
    p = products[SLUG]
    assert p["name"] == "OcuHeal+ Eye Drops", "the invoice matches FileMaker by exact name"
    assert p["fmp_id"] == "1200"
    assert p["price_cents"] == 6997
    assert p["bottle_type"] == "Dropper 5 mL"
    assert p["qty_pricing"] is True
    assert p["url"] == f"https://myhealingoasis.com/begin/product/{SLUG}"


def test_the_panel_is_the_spec_in_order(products):
    assert [(i["name"], i["dose"]) for i in products[SLUG]["ingredients"]] == PANEL


def test_the_base_gives_up_exactly_the_added_dmso(products):
    """Read from the catalog: the 9.5% DMSO came out of the base, so the two lines
    still sum to OcuHeal's 96% + 0.5%."""
    doses = {i["name"]: i["dose"] for i in products[SLUG]["ingredients"]}
    base = float(doses["Quintessential Bioterrain Restore"].rstrip("%"))
    dmso = float(doses["DMSO (Dimethylsulfoxide)"].rstrip("%"))
    assert (base, dmso) == (86.5, 10.0)
    assert base + dmso == 96.5


def test_the_directions_are_filemakers_dosage(products):
    assert products[SLUG]["directions"] == DIRECTIONS


def test_ocuheal_stays_as_it_was(products):
    p = products["ocuheal-eye-drops"]
    assert p["name"] == "OcuHeal Eye Drops"
    assert p["fmp_id"] == "493"
    assert p["price_cents"] == 6997
    assert p["bottle_type"] == "Dropper 5 mL"
    assert p["url"].endswith("/begin/product/ocuheal-eye-drops")
    # Its panel was corrected on 2026-09-25 (test_ocuheal_panels_match_the_bottle.py);
    # this test pins only its identity.


def test_the_new_entry_invents_no_old_store_link_or_compare_price(products):
    p = products[SLUG]
    assert p.get("no_groovekart") is True
    assert "legacy_store_url" not in p
    assert "regular_cents" not in p


def test_the_description_is_never_empty(products):
    """An empty description renders an empty Overview box."""
    assert products[SLUG]["description"].strip()


def test_page_data_serves_the_panel_and_directions(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    data = c.get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = next(s for s in data["sections"] if s["id"] == "ingredients")
    assert [(i["name"], i["dose"]) for i in sec["body"]["ingredients"]] == PANEL
    assert sec["body"]["directions"] == DIRECTIONS
    assert c.get(f"/begin/product/{SLUG}").status_code == 200


def test_filemaker_1200_maps_to_the_new_slug():
    m = json.load(open(os.path.join(ROOT, "data", "fmp_slug_map.json")))["resolved"]
    assert m["1200"] == SLUG
    assert m["493"] == "ocuheal-eye-drops"


APPROVED_INTRO = (
    "OcuHeal+ is OcuHeal Eye Drops with more DMSO. It carries the same botanical and "
    "nutritional ingredients for the whole eye, including the retina, lens, cornea, "
    "conjunctiva, lacrimal glands and tear film. DMSO rises from 0.5% to 10%, and the "
    "Quinton sea water base (Quintessential Bioterrain Restore) makes room for it, from "
    "96% to 86.5%. Use 1 drop in each eye 2 times a day."
)


def test_the_description_is_glens_approved_intro(products):
    """Glen, 2026-09-24, in production's tab: "approve"."""
    assert products[SLUG]["description"] == APPROVED_INTRO
