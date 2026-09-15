"""Sleep Synergy is renamed Sleep Syntropy, 2026-09-15.

Glen: "rename sleep syntropy", then "descriptions, teaching data and the search title should say
Sleep Syntropy". So name, pinecone_title, descriptions and the Atlas data all carry the new name.
The old name stays reachable as an alias and a glossary override, so past orders, scans and
older text still resolve. The product is pinned to QuickBooks item 48 ("Sleep Synergy"), so a
revived booking path cannot create a third item by name.
"""
import json
import pathlib

from dashboard import biofield_invoice as bi
from dashboard import clinical_glossary as cg
from dashboard import shipping

PRODUCTS = json.load(open("data/products.json", encoding="utf-8"))["products"]
CATALOG = [dict(p, slug=s) for s, p in PRODUCTS.items()]

# Where the old name may still appear on purpose: lookups that keep it resolving.
OLD_NAME_ALLOWED = {
    "data/products.json": 1,                   # aliases: ["Sleep Synergy"]
    "data/clinical_remedy_overrides.json": 1,  # "Sleep Synergy": "sleep-syntropy"
}


def test_the_record_carries_the_new_name_everywhere_a_reader_sees_it():
    p = PRODUCTS["sleep-syntropy"]
    assert p["name"] == "Sleep Syntropy"
    assert p["pinecone_title"] == "Sleep Syntropy"
    assert p["aliases"] == ["Sleep Synergy"]
    assert p["qbo_item_id"] == "48"
    assert not p.get("inactive")
    assert "Sleep Synergy" not in p["description"]


def test_no_data_file_names_sleep_synergy_outside_the_old_name_lookups():
    counts = {}
    for f in sorted(pathlib.Path("data").rglob("*.json")):
        n = f.read_text(encoding="utf-8").count("Sleep Synergy")
        if n:
            counts[f.as_posix()] = n
    assert counts == OLD_NAME_ALLOWED


def test_both_names_resolve_to_the_product():
    idx = cg.product_name_index(PRODUCTS)
    overrides = cg.load_overrides()
    assert cg.remedy_product_slug("Sleep Syntropy", idx, overrides) == "sleep-syntropy"
    # The glossary index reads name and slug only, so the old name needs the override.
    assert cg.remedy_product_slug("Sleep Synergy", idx, overrides) == "sleep-syntropy"


def test_a_biofield_invoice_line_named_sleep_syntropy_is_billed():
    # FMP calls it Sleep Syntropy. The invoice matches exact names only, so before the
    # rename this line was skipped from the invoice.
    assert bi.resolve_line_slug("Sleep Syntropy", CATALOG) == "sleep-syntropy"


def test_the_sleep_bundle_still_packs_sleep_syntropy_by_slug_and_by_name():
    bundle = dict(PRODUCTS["sleep-bundle"], slug="sleep-bundle")
    assert "sleep-syntropy" in [c["slug"] for c in shipping.bundle_component_products(bundle, CATALOG)]
    by_name = {k: v for k, v in bundle.items() if k != "bundle_component_slugs"}
    assert "sleep-syntropy" in [c["slug"] for c in shipping.bundle_component_products(by_name, CATALOG)]
