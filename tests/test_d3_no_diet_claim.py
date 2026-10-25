"""No vegetarian or vegan claim while a product's vitamin D3 source is unknown.

Glen, 2026-10-25: FileMaker raw 5313 names no source, and cholecalciferol is usually from
lanolin or fish oil (production/05 Formulations/_store-updates/
2026-10-25-d3-no-vegetarian-claim.md). The claim returns only with a source certificate.
"""
import json
import os
import re

import pytest

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
# The FileMaker ids whose recipe uses raw 5313 (extract of 2026-10-19), plus 327. B17 Syntropy (317)
# carries no fmp_id here, so it is named by slug.
# Dental Powder (327) has no recipe lines; its July 2026 batch used D3 of the same
# 500,000 IU/g strength (formulation, 2026-10-25).
FMP_IDS = {"327", "337", "366", "367", "378", "395", "429", "431", "449", "469", "1104",
           "1190", "1196"}
SLUGS = {"b17-syntropy"}
CLAIM = re.compile(r"(?i)\b(vegan|vegetarian)\b")


def _load(name):
    return json.load(open(os.path.join(DATA, name)))


def _d3_slugs():
    products = _load("products.json")["products"]
    return {s for s, r in products.items() if str(r.get("fmp_id")) in FMP_IDS} | SLUGS


def test_every_listed_product_is_found():
    assert len(_d3_slugs()) == 14


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_no_record_claims_a_diet(name):
    doc = _load(name)
    records = doc.get("products", doc)
    hits = [s for s in _d3_slugs() if CLAIM.search(json.dumps(records.get(s, {})))]
    assert hits == []


@pytest.mark.parametrize("name", ["atlas-concepts.json", "atlas-seed-input.json"])
def test_no_atlas_summary_claims_a_diet(name):
    slugs, hits = _d3_slugs(), []

    def walk(o):
        if isinstance(o, dict):
            if o.get("id") in slugs and CLAIM.search(json.dumps(o)):
                hits.append(o["id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(_load(name))
    assert hits == []
