"""Sleep Synergy is renamed Sleep Syntropy, 2026-09-15 (Glen: "rename sleep syntropy").

The Iron Syntropy pattern: the display name changes and pinecone_title keeps the old name, so
the stored vectors and every old-name lookup still match. The product is pinned to QuickBooks
item 48 ("Sleep Synergy"), so a revived booking path cannot create a third item by name.
"""
import json

from dashboard import biofield_invoice as bi
from dashboard import clinical_glossary as cg
from dashboard import shipping

PRODUCTS = json.load(open("data/products.json", encoding="utf-8"))["products"]
CATALOG = [dict(p, slug=s) for s, p in PRODUCTS.items()]


def test_the_record_carries_the_new_name_and_keeps_its_vector_title():
    p = PRODUCTS["sleep-syntropy"]
    assert p["name"] == "Sleep Syntropy"
    assert p["pinecone_title"] == "Sleep Synergy"
    assert p["qbo_item_id"] == "48"
    assert not p.get("inactive")


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
