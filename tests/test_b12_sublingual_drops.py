"""Vitamin B12 Sublingual Drops replace the powder on the same listing.

Spec: production/05 Formulations/b12-sublingual-drops/2026-09-24/listing-spec.md. Glen
approved the label (panel and directions), the FileMaker 323 rename and the product
rulings: the powder "absorbs too much moisture to be stable", "same ingredients other
than Microwater", "10 drops per dose", "once a day", "same price". Glen edited the intro
on 2026-09-30 ("Hold up to 10 drops... otherwise as above"); it is pinned.
"""
import importlib
import json
from pathlib import Path

import pytest

from dashboard import biofield_invoice as bi

ROOT = Path(__file__).resolve().parent.parent
SLUG = "sublingual-b12"
TWIN = "sublingual-b12-powder"
NAME = "Vitamin B12 Sublingual Drops"
OLD_NAME = "Vitamin B12 Sublingual Powder"
DIRECTIONS = "Hold up to 10 drops under the tongue once a day, early in the day, or as guided."


def _catalog():
    return json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["products"]


def _live_catalog():
    return [{"slug": k, "name": v.get("name"), "inactive": v.get("inactive")}
            for k, v in _catalog().items()
            if not v.get("inactive") and not v.get("superseded_by")]


# --- the listing ---------------------------------------------------------------------

def test_the_listing_carries_filemakers_name_price_and_record():
    p = _catalog()[SLUG]
    assert p["name"] == NAME           # the invoice reads FileMaker by this exact name
    assert p["price_cents"] == 6997
    assert p["fmp_id"] == "323"
    assert p["qty_pricing"] is True    # unchanged by the switch
    assert not p.get("inactive") and not p.get("superseded_by")


def test_the_bottle_is_the_30_ml_dropper_type_other_listings_use():
    cat = _catalog()
    assert cat[SLUG]["bottle_type"] == "30ml"
    assert sum(1 for p in cat.values() if p.get("bottle_type") == "30ml") > 1


GLENS_INTRO = (
    "Vitamin B12 Sublingual Drops carry the coenzyme forms of B12, adenosylcobalamin and "
    "methylcobalamin, at a 100 to 1 ratio, with D-ribose, in Microwater. They replace our B12 "
    "powder, which took up moisture from the air. Hold up to 10 drops under the tongue once a "
    "day, early in the day.")


def test_glens_intro_is_pinned_word_for_word():
    p = _catalog()[SLUG]
    assert p["intro"] == GLENS_INTRO
    assert p["description"] == GLENS_INTRO
    assert {"intro", "description"} <= set(p["copy_pinned"])


def test_the_page_serves_the_pinned_intro(appmod):
    # A conflicting AI draft under the slug must never replace Glen's words (review round 2).
    import sqlite3
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec in ("intro", "description"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER powder scoop")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    assert "AI DRAFT MARKER" not in json.dumps(d)
    secs = {x["id"]: x for x in d["sections"]}
    for sid in ("intro", "description"):
        assert secs[sid]["body"] == GLENS_INTRO
        assert "ai" not in secs[sid], sid


def test_the_panel_per_10_drops():
    ings = _catalog()[SLUG]["ingredients"]
    assert ings == [
        {"name": "Vitamin B12 (Adenosyl & Methyl Cobalamin)", "dose": "337 mcg (333 mcg adenosylcobalamin, 3.3 mcg methylcobalamin)"},
        {"name": "D-Ribose", "dose": "277 mg"},
    ]
    assert _catalog()[SLUG]["ingredients_source"] == "label-2026-09-24"


def test_the_directions_are_the_label_line():
    assert _catalog()[SLUG]["directions"] == DIRECTIONS


def test_the_description_names_no_price_powder_or_scoop():
    d = _catalog()[SLUG]["description"]
    assert d.startswith(NAME)
    low = d.lower()
    assert "price:" not in low and "$" not in d
    assert "scoop" not in low
    # The one mention of the powder is the sentence saying the drops replace it.
    assert low.count("powder") == 1 and "replace our b12 powder" in low


def test_the_chat_alias_follows_the_new_name():
    aliases = json.loads((ROOT / "data" / "product-aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["Sublingual B12"]["catalog_name"] == NAME


def test_the_retired_twin_still_points_at_the_listing():
    t = _catalog()[TWIN]
    assert t["inactive"] is True and t["superseded_by"] == SLUG
    assert t["name"] == "Sublingual B12 Powder"   # old reports still read


# --- invoices ------------------------------------------------------------------------

def test_the_invoice_resolves_the_new_name():
    assert bi.resolve_line_slug(NAME, _live_catalog()) == SLUG


def test_an_old_powder_line_still_invoices_the_drops():
    assert bi._RETIRED_NAMES["vitamin b12 sublingual powder"] == SLUG
    assert bi.resolve_line_slug(OLD_NAME, _live_catalog()) == SLUG
    # The picker offers the FMP snapshot's name until the snapshot refreshes (review round 1).
    assert bi.resolve_line_slug("Sublingual B12 Powder", _live_catalog()) == SLUG


# --- routes --------------------------------------------------------------------------

@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", "true")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True, raising=False)
    monkeypatch.setattr(a, "_RELATED_PRODUCTS_ENABLED", False, raising=False)
    # No pinned copy, so page-data would generate a card: never call out.
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")
    # No override row may turn the dropper back into something else.
    monkeypatch.setattr(a, "_bottle_type_override", lambda slug: "")
    return a


def test_capsule_formats_and_refills_never_attach(appmod):
    p = dict(_catalog()[SLUG], slug=SLUG)
    assert appmod._capsule_formats_ok(p) is False
    assert appmod._clean_format(p, "refill") == ""
    assert appmod._clean_format(p, "larger") == ""


def test_product_data_offers_no_capsule_formats(appmod):
    d = appmod.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["name"] == NAME
    assert d.get("formats") is None


def test_the_powder_url_302s_to_the_drops_keeping_the_query(appmod):
    r = appmod.app.test_client().get(f"/begin/product/{TWIN}?utm_source=x&z=1")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/begin/product/{SLUG}?utm_source=x&z=1")


def test_page_data_serves_the_directions(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    ing = next(s for s in d["sections"] if s["id"] == "ingredients")
    assert ing["body"]["directions"] == DIRECTIONS
    assert [i["name"] for i in ing["body"]["ingredients"]][-1] == "D-Ribose"


def test_every_glossary_b12_remedy_name_resolves_to_the_drops():
    """Review round 3: a typo in a glossary remedy name passed every test. Each B12 remedy
    in both glossary files must resolve, by the glossary's own resolver, to this listing."""
    from dashboard import clinical_glossary as cg
    products = _catalog()
    idx = cg.product_name_index({s: p for s, p in products.items() if not p.get("inactive")})
    found = 0
    for path in ("data/clinical_theory_catalog.json", "data/e4l_stressor_map.json"):
        for name in _remedy_names(json.load(open(path))):
            if "b12 sublingual" in name.lower():
                found += 1
                assert cg.remedy_product_slug(name, idx) == SLUG, (path, name)
    assert found == 4   # plus 2 mentions inside descriptions: 6 uses in all


def _remedy_names(node):
    """Every "name" value in the file: the two glossaries nest remedies differently."""
    if isinstance(node, dict):
        if isinstance(node.get("name"), str):
            yield node["name"]
        for v in node.values():
            yield from _remedy_names(v)
    elif isinstance(node, list):
        for v in node:
            yield from _remedy_names(v)
