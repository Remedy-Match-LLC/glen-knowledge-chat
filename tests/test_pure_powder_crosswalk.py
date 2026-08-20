import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pure_powder_crosswalk import build_crosswalk, catalog_match, review_gate


def test_gingerols_20_maps_to_generic_gingerol_as_specification_mismatch():
    catalog = {"gingerol": {"name": "Gingerol", "price_cents": 3997}}
    powders = [{
        "id_pk": "4035", "name_raw": "Gingerols 20% (Zingiber officinalis)",
        "zc_inventory": "860.8",
    }]
    rows = build_crosswalk([], powders, catalog)
    assert rows[0]["catalog_slug"] == "gingerol"
    assert rows[0]["status"] == "specification_mismatch"
    assert rows[0]["decision"] == "rename_or_split_catalog_record"


def test_established_pure_powder_missing_from_catalog_is_priority():
    pure = [{
        "id_pk": "1", "product_name": "Chlorella Powder", "sold_size": "120",
        "sold_measurement": "g", "sold_price": "40",
    }]
    row = build_crosswalk(pure, [], {})[0]
    assert row["status"] == "missing_catalog_record"
    assert row["sold_size"] == "120 g"


def test_tiny_dose_and_restricted_materials_are_not_auto_publishable():
    assert review_gate("Vitamin B9: 5-MTHF 98%")[0] == "formula_only"
    assert review_gate("Progesterone 99%")[0] == "exclude"
    assert review_gate("Gingerols 20% (Zingiber officinalis)")[0] == "manual_review"


def test_exact_catalog_match_is_case_and_whitespace_insensitive():
    slug, item = catalog_match("  SUMAC   Bran 50:1 ", {"sumac": {"name": "Sumac Bran 50:1"}})
    assert slug == "sumac"
    assert item["name"] == "Sumac Bran 50:1"


def test_known_sumac_retail_suffix_maps_to_established_fmp_product():
    slug, item = catalog_match(
        "Sumac Bran 50:1",
        {"sumac-bran-501-pure-powder": {"name": "Sumac Bran 50:1 Pure Powder"}},
    )
    assert slug == "sumac-bran-501-pure-powder"
    assert item["name"].endswith("Pure Powder")


def test_magnesium_glycinate_is_a_sellable_catalog_record():
    products_path = Path(__file__).resolve().parent.parent / "data" / "products.json"
    product = json.loads(products_path.read_text())["products"]["magnesium-glycinate"]
    assert product["name"] == "Magnesium Glycinate"
    assert product["price_cents"] == 4000
    assert product["fmp_id"] == "1113"
    assert product["qty_pricing"] is False
