"""Estrogen and Fibrin Balance Program, with the Fibrosolve, Fibrolysis Factors and
Estro-Clear fixes (spec production/05 Formulations/_store-updates/
2026-10-09-estrogen-fibrin-balance-program.md, approved by Glen 2026-10-09).

The bottles on the shelf carry Fibrosolve's and Fibrolysis Factors' June 2026 labels,
so the store follows them. Every text here is Glen's approved wording, pinned verbatim.
The chat quotes a pinned product's description, which is why the old doses had to go
from `description` as well as `directions`.
"""
import importlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = "estrogen-fibrin-balance-program"
THREE = ("fibrosolve", "fibrolysis-factors", "estro-clear")

FS_DESC = ("Systemic proteolytic enzyme complex with a proprietary bromelain and protease blend. "
           "Take 1 to 2 capsules a day between meals, or as guided. Not for use with anticoagulant "
           "or antiplatelet medication, with a bleeding disorder, during pregnancy, or within about "
           "two weeks of surgery.")
FS_DIR = "Take 1 to 2 capsules a day between meals, or as guided."
FS_WARN = ("Not for use with anticoagulant or antiplatelet medication, with a bleeding disorder, "
           "during pregnancy, or within about two weeks of surgery.")
FF_DESC = ("Botanical formula, five standardized actives at 487.5 mg per capsule. Take 1 to 3 "
           "capsules a day with food, or as guided.")
FF_DIR = "Take 1 to 3 capsules a day with food, or as guided."
EC_DESC = ("Estrogen metabolism and clearance support, nine actives at about 497 mg per capsule. "
           "Third SKU of the Fibrolysis line, alongside Fibrosolve and Fibrolysis Factors.")
EC_DIR = ("Take 1 capsule 1 to 2 times daily with oils or a fatty meal. Keep the bottle tightly "
          "closed and dry.")
EC_WARN = ("Contains piperine, which can raise blood levels of some medications. Consult your "
           "health professional before use with medications, thyroid or hormone-sensitive "
           "conditions, pregnancy, or nursing.")
BUNDLE_INTRO = (
    "The Estrogen and Fibrin Balance Program includes Fibrosolve, Fibrolysis Factors and "
    "Estro-Clear. Take Fibrosolve 1 to 2 capsules a day between meals. Take Fibrolysis Factors 1 "
    "to 3 capsules a day with food. Take Estro-Clear 1 capsule 1 to 2 times a day with oils or a "
    "fatty meal. Each product may be taken as guided.\n\n"
    "Cautions: Fibrosolve is not for use with anticoagulant or antiplatelet medication, with a "
    "bleeding disorder, during pregnancy, or within about two weeks of surgery. Estro-Clear "
    "contains piperine, which can raise blood levels of some medications. Consult your health "
    "professional before use with medications, thyroid or hormone-sensitive conditions, "
    "pregnancy, or nursing.")
BUNDLE_DESC = ("Fibrosolve, Fibrolysis Factors and Estro-Clear together, for 10% less than the "
               "three bought separately.")
SERVED_ALLOWLIST = {"intro", "description", "reviews", "cta", "related"}


@pytest.fixture(scope="module")
def products():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))


@pytest.mark.parametrize("slug,desc,directions,warning", [
    ("fibrosolve", FS_DESC, FS_DIR, FS_WARN),
    ("fibrolysis-factors", FF_DESC, FF_DIR, None),
    ("estro-clear", EC_DESC, EC_DIR, EC_WARN),
])
def test_label_texts_are_exact(products, slug, desc, directions, warning):
    p = products[slug]
    assert p["description"] == desc
    assert p["directions"] == directions
    assert p.get("warning") == warning


def test_fibrosolve_no_longer_says_empty_stomach(products):
    p = products["fibrosolve"]
    assert "empty stomach" not in p["description"].lower()
    assert "empty stomach" not in p["directions"].lower()
    assert "anti-fibrotic" not in p["description"].lower()
    assert "anti-fibrotic" not in products["fibrolysis-factors"]["description"].lower()


@pytest.mark.parametrize("slug", THREE)
def test_the_corrections_file_matches_so_enrichment_cannot_revert(products, corrections, slug):
    for field in ("description", "directions", "warning", "ingredients"):
        assert corrections[slug].get(field) == products[slug].get(field), (slug, field)


@pytest.mark.parametrize("slug", THREE + (BUNDLE,))
def test_content_since_is_a_utc_timestamp(products, slug):
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", products[slug]["content_since"])


def test_the_pins_carry_these_texts_into_the_chat(products):
    assert products["fibrosolve"]["copy_pinned"] == ["description", "ingredients"]
    assert products["fibrolysis-factors"]["copy_pinned"] == ["description"]
    assert products["estro-clear"]["copy_pinned"] == ["description"]
    assert products["fibrosolve"]["ingredients_source"] == "label-2026-06"


def test_fibrosolve_is_drcaps():
    from dashboard.capsule_copy import capsule_kind
    assert capsule_kind("fibrosolve") == "drcaps"


def test_the_bundle_record(products):
    b = products[BUNDLE]
    assert b["name"] == "Estrogen and Fibrin Balance Program"
    assert b["bundle"] is True and b["price_rule"] == "components_less_10pct"
    assert b["price_cents"] == 18900 and b["autoship_eligible"] is True
    assert b["bundle_component_slugs"] == [{"slug": s, "qty": 1} for s in THREE]
    assert b["intro"] == BUNDLE_INTRO
    assert b["description"] == BUNDLE_DESC
    assert b["ingredients"] == [] and b["benefits"] == []
    assert b["how_it_works"] == "" and b["research"] == ""
    assert b["copy_pinned"] == ["intro", "description", "research", "ingredients", "benefits"]
    assert "bundle_description" not in b
    assert "fibroid" not in json.dumps(b).lower()
    from dashboard.sales_images import ai_images_off
    assert ai_images_off(BUNDLE)


def test_a_bundle_with_no_research_text_drops_the_research_section():
    from dashboard.product_page_sections import filter_sections
    secs = [{"id": i} for i in ("intro", "description", "research", "cta")]
    ids = lambda **kw: [s["id"] for s in filter_sections(secs, has_ingredients=False,
                                                          has_own_video=False, **kw)]
    assert ids(is_bundle=True, has_research_text=False) == ["intro", "description", "cta"]
    assert "research" in ids(is_bundle=True, has_research_text=True)
    assert "research" in ids(is_bundle=False, has_research_text=False)


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    """Its own data dir and a fresh catalog from data/products.json, so this does not
    depend on what earlier tests in the run did to app's in-memory products."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    return appmod


def test_the_bundle_page_serves_only_its_approved_sections(fresh_app):
    c = fresh_app.app.test_client()
    data = c.get(f"/begin/product-page-data/{BUNDLE}").get_json()
    ids = [s["id"] for s in data["sections"]]
    assert set(ids) <= SERVED_ALLOWLIST, ids
    assert {"intro", "description", "cta"} <= set(ids)
    assert not any(s.get("ai") for s in data["sections"])
    intro = next(s for s in data["sections"] if s["id"] == "intro")["body"]
    assert intro == BUNDLE_INTRO
    pd = c.get(f"/begin/product-data/{BUNDLE}").get_json()
    assert pd["price_cents"] == 18900


def test_fibrosolve_page_serves_the_label_panel_and_drcaps(fresh_app):
    data = fresh_app.app.test_client().get("/begin/product-page-data/fibrosolve").get_json()
    body = next(s for s in data["sections"] if s["id"] == "ingredients")["body"]
    assert [i["name"] for i in body["ingredients"]][-1] == "Protease (Aspergillus niger)"
    assert body["directions"] == FS_DIR and body["warning"] == FS_WARN
    from dashboard.capsule_copy import CAPSULE_COPY
    assert body["capsule"]["text"] == CAPSULE_COPY["drcaps"]["text"]
    assert "pullulan" not in json.dumps(body).lower()


@pytest.mark.parametrize("question,directions", [
    ("How much Fibrolysis Factors should I take?", FF_DIR),
    ("How much Estro-Clear should I take?", EC_DIR),
    ("How much Fibrosolve should I take?", FS_DIR),
])
def test_the_chat_carries_each_products_label_dose(fresh_app, question, directions):
    block = fresh_app._named_product_facts(question)
    assert block and directions in block, question
