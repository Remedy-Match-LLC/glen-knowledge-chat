"""A remedy named for a retired listing bills the live product that replaced it.

Glen, 2026-10-13: Linda Maher's layer named FileMaker 372 "Clear Lens Eye Drops
ACES+CAT Eye Drops". The live product is "Clear Lens Eyedrops", so her invoice
skipped the drops.
"""
import json

import pytest

import dashboard.biofield_authoring as A
from dashboard import biofield_invoice as B

PRODUCTS = {"products": {
    "clear-lens-eye-drops": {"name": "Clear Lens Eyedrops"},
    "clear-lens-eye-drops-aces-cat-eye-drops": {
        "name": "Clear Lens Eye Drops", "pinecone_title": "Clear Lens Eye Drops ACES+CAT Eye Drops",
        "fmp_id": "372", "inactive": True, "superseded_by": "clear-lens-eye-drops"},
    # A chain of two retirements ends at the live product.
    "older": {"name": "Old Lens Drops", "inactive": True,
              "superseded_by": "clear-lens-eye-drops-aces-cat-eye-drops"},
    # Retired with no replacement: bills nothing.
    "gone": {"name": "Gone Drops", "inactive": True},
    # Two retired listings answer to one name but point at different products.
    "twin-a": {"name": "Twin", "inactive": True, "superseded_by": "a-live"},
    "twin-b": {"name": "Twin", "inactive": True, "superseded_by": "b-live"},
    "a-live": {"name": "A Live"}, "b-live": {"name": "B Live"},
    # A bare code never bills.
    "mb5-old": {"name": "MB 5", "inactive": True, "superseded_by": "a-live"},
}}

CATALOG = [{"slug": "clear-lens-eye-drops", "name": "Clear Lens Eyedrops", "price_cents": 7000},
           {"slug": "a-live", "name": "A Live", "price_cents": 4000},
           {"slug": "b-live", "name": "B Live", "price_cents": 4000}]


@pytest.fixture(autouse=True)
def _products(tmp_path, monkeypatch):
    p = tmp_path / "products.json"
    p.write_text(json.dumps(PRODUCTS))
    monkeypatch.setattr(A, "_PRODUCTS_JSON", str(p))


@pytest.mark.parametrize("name", [
    "Clear Lens Eye Drops ACES+CAT Eye Drops",   # the FileMaker name on Linda's layer
    "clear lens eye drops",                      # the retired listing's own name
    "Old  Lens Drops",                           # two retirements deep, spacing ignored
])
def test_a_retired_name_bills_the_live_product(name):
    assert B.resolve_line_slug(name, CATALOG) == "clear-lens-eye-drops"


@pytest.mark.parametrize("name", ["Gone Drops", "Twin", "MB 5", "Clear Lens"])
def test_no_replacement_a_clash_a_bare_code_or_a_near_name_bills_nothing(name):
    assert B.resolve_line_slug(name, CATALOG) is None


def test_a_replacement_off_the_catalog_bills_nothing():
    assert B.resolve_line_slug("Clear Lens Eye Drops ACES+CAT Eye Drops", CATALOG[1:]) is None


def test_lindas_invoice_now_carries_the_drops():
    built = B.build_invoice_lines({}, [{"name": "Clear Lens Eye Drops ACES+CAT Eye Drops", "qty": 1}],
                                  CATALOG, include_fee=False)
    assert built["lines"] == [{"slug": "clear-lens-eye-drops", "qty": 1, "source": "biofield"}]
    assert built["skipped"] == []


def test_one_word_names_bill_nothing(tmp_path, monkeypatch):
    data = json.loads(json.dumps(PRODUCTS))
    data["products"]["emf-old"] = {"name": "EMF", "inactive": True, "superseded_by": "a-live"}
    p = tmp_path / "p2.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(A, "_PRODUCTS_JSON", str(p))
    assert B.resolve_line_slug("EMF", CATALOG) is None


def test_a_dead_end_chain_does_not_hide_a_good_replacement(tmp_path, monkeypatch):
    data = json.loads(json.dumps(PRODUCTS))
    data["products"]["twin-b"]["superseded_by"] = "nowhere"      # dead end
    p = tmp_path / "p3.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(A, "_PRODUCTS_JSON", str(p))
    data["products"]["twin-a"]["name"] = data["products"]["twin-b"]["name"] = "Twin Drops"
    p.write_text(json.dumps(data))
    assert B.resolve_line_slug("Twin Drops", CATALOG) == "a-live"


def test_an_active_product_ends_the_chain(tmp_path, monkeypatch):
    data = json.loads(json.dumps(PRODUCTS))
    data["products"]["clear-lens-eye-drops"]["superseded_by"] = "a-live"   # active, not retired
    p = tmp_path / "p4.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(A, "_PRODUCTS_JSON", str(p))
    assert B.resolve_line_slug("Clear Lens Eye Drops ACES+CAT Eye Drops", CATALOG) == "clear-lens-eye-drops"


def test_two_sources_naming_two_products_bill_neither(monkeypatch):
    monkeypatch.setattr(B, "_old_names", lambda: {"clear lens eye drops": "a-live"})
    assert B.resolve_line_slug("Clear Lens Eye Drops", CATALOG) is None
