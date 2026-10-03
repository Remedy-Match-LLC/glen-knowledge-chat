"""Spike Shield: the August 2026 six-ingredient formula in a pullulan capsule.

Production spec, 2026-10-03 (production/05 Formulations/spike-shield/2026-10-03/
store-update-spec.md). Glen confirmed the bottle holds it; FileMaker 514 holds it.
`content_since` drops AI text generated before the change, which named 28 old
ingredients and "enteric"."""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIX = [
    ("N-Acetyl-L-Cysteine (NAC)", "150 mg"),
    ("Curcumin 95% (Curcuma longa) (rhizoma)", "150 mg"),
    ("Quercetin Dihydrate 98%", "150 mg"),
    ("Coenzyme Q10 (Ubiquinone) 98%", "30 mg"),
    ("Black Seed Extract 20% TQ (Nigella sativa) (semen)", "15 mg"),
    ("Piperine 95% (Piper nigrum)", "2 mg"),
]


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)["spike-shield"]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_six_ingredients_in_order(fname):
    assert [(i["name"], i["dose"]) for i in _entry(fname)["ingredients"]] == SIX


def test_the_description_has_the_new_directions_and_no_old_formula():
    d = _entry()["description"]
    assert d.endswith("Take 1 capsule daily with food, or as guided.")
    assert "Full spectrum support in the face of toxic spike protein production" in d
    for gone in ("enteric", "empty stomach", "one to three", "Iodine", "Zeolite"):
        assert gone not in d
    e = _entry()
    assert e["name"] == "Spike Shield" and e["content_since"] == "2026-10-03T00:00:00"


def test_cached_product_content_from_before_the_change_is_regenerated(monkeypatch):
    from dashboard import product_content as pc
    old = {"content": {"text": "OLD enteric zeolite"}, "sources": [],
           "generated_at": "2026-09-01T00:00:00", "cached": True}
    monkeypatch.setattr(pc, "_cache_get", lambda slug, ct: dict(old))
    monkeypatch.setattr(pc, "_page_text", lambda p: {"text": "PINECONE OLD PAGE", "url": ""})
    seen = {}

    def gen(product, page):
        seen["page"] = page["text"]
        return {"text": "NEW"}

    monkeypatch.setattr(pc, "_generate_how_it_works", gen)
    monkeypatch.setattr(pc, "_cache_put", lambda *a: None)
    prod = dict(_entry(), slug="spike-shield")
    out = pc.get_or_generate(prod, "how_it_works")
    assert out["content"] == {"text": "NEW"}
    assert "PINECONE OLD PAGE" not in seen["page"] and "Quercetin" in seen["page"]
    # A hit generated after the change is served as is.
    old["generated_at"] = "2026-10-04T00:00:00"
    assert pc.get_or_generate(prod, "how_it_works")["content"]["text"].startswith("OLD")


def test_sales_drafts_older_than_the_change_are_reset_once():
    from dashboard import sales_pages as sp
    cx = sqlite3.connect(":memory:")
    sp.init_table(cx)
    sp.upsert_section(cx, "spike-shield", "intro", "OLD enteric intro")
    cx.execute("UPDATE sales_pages SET generated_at='2026-09-01T00:00:00'")
    cx.commit()
    assert sp.reset_if_older(cx, "spike-shield", "2026-10-03T00:00:00") is True
    assert sp.get_section(cx, "spike-shield", "intro") is None
    sp.upsert_section(cx, "spike-shield", "intro", "NEW intro")
    assert sp.reset_if_older(cx, "spike-shield", "2026-10-03T00:00:00") is False
    assert sp.get_section(cx, "spike-shield", "intro") == "NEW intro"
    assert sp.reset_if_older(cx, "spike-shield", "") is False


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "", "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def test_the_page_drops_the_old_ai_intro(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.init_table(cx)
        sp.upsert_section(cx, "spike-shield", "intro", "OLD enteric intro")
        cx.execute("UPDATE sales_pages SET generated_at='2026-09-01T00:00:00'")
        cx.commit()
    d = appmod.app.test_client().get("/begin/product-page-data/spike-shield").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert "OLD enteric" not in json.dumps(d)
    assert secs["intro"].get("ai") == "pending"
    ings = secs["ingredients"]["body"]["ingredients"]
    assert [(i["name"], i["dose"]) for i in ings] == SIX


def test_overview_starts_without_the_scrape_artefact():
    import json
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "data" / "products.json"
    d = json.loads(p.read_text(encoding="utf-8"))["products"]["spike-shield"]["description"]
    assert d.startswith("Full spectrum support in the face of"), d[:60]
