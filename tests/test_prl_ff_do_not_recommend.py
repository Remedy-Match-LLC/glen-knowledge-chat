"""The PRL and Fullscript cards suggest a Functional Formulation beside each channel
product. The suggestion comes from a vault crosswalk, which named Electrolyte Mineral
Manna and AllerFree (never recommended) and retired products. The card view must drop
any suggestion that is blocked or not sellable (2026-10-07)."""
import json
import os

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import pytest

import app
from dashboard.related_products import DO_NOT_RECOMMEND

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _names_of_blocked_products():
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    return sorted({prods[s]["name"] for s in DO_NOT_RECOMMEND if s in prods})


@pytest.mark.parametrize("name", ["Electrolyte Mineral Manna", "AllerFree", "Fungifuge"])
def test_named_blocked_products_are_not_suggested(name):
    assert app._prl_ff_view(name, "equivalent") is None
    assert app._fullscript_ff_view(name, "equivalent") is None


@pytest.mark.parametrize("name", _names_of_blocked_products())
def test_every_blocked_catalog_name_is_not_suggested(name):
    assert app._prl_ff_view(name, "consider") is None


def test_a_name_that_is_no_product_is_not_suggested():
    assert app._prl_ff_view("NeurOmega", "consider") is None


def test_a_retired_product_with_no_successor_is_not_suggested():
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    retired = [p["name"] for p in prods.values()
               if p.get("inactive") and not p.get("superseded_slug")
               and not app._resolve_remedy_slug({"name": p["name"]})]
    assert retired, "fixture: expected at least one retired product with no successor"
    for name in retired[:20]:
        assert app._prl_ff_view(name, "consider") is None, name


def test_a_sellable_product_is_still_suggested():
    v = app._prl_ff_view("Clear the Way", "consider")
    assert v == {"name": "Clear the Way", "relation": "consider", "slug": "clear-the-way"}
    assert app._fullscript_ff_view("Clear the Way", None)["relation"] == "consider"


def test_no_suggestion_in_the_committed_prl_seed_is_blocked():
    seed = json.load(open(os.path.join(ROOT, "data", "prl_seed.json")))
    for p in seed["products"]:
        v = app._prl_ff_view(p.get("best_ff"), p.get("relation"))
        if v:
            assert v["slug"] not in DO_NOT_RECOMMEND, p["name"]


def test_the_chip_shows_the_name_of_the_product_it_links_to():
    v = app._prl_ff_view("Relax", "consider")
    assert v["slug"] == "stress-release"
    assert v["name"] == "Stress Release"
