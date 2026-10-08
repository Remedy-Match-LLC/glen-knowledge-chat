"""AngiogenX: the October 2026 five-ingredient eye formula, one capsule a day.

Spec: production/05 Formulations/_store-updates/2026-10-06-angiogenx.md (Glen "send it",
2026-10-07, via formulation). FileMaker 411 holds it. The copy is pinned, benefits and
how-it-works are empty until Knowledge replaces the Pinecone text, and the three approved
photos go in the `images` gallery.
"""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "angiogenx"
FIVE = [
    ("Pine Bark OPCs 95% (Pinus pinaster) (cortex)", "158 mg"),
    ("Trans-Resveratrol 98% (Polygonum cuspidatum) (radix)", "100 mg"),
    ("Curcumin 95% (Curcuma longa) (rhizoma)", "100 mg"),
    ("Notoginsenosides 80% (Panax notoginseng) (radix)", "75 mg"),
    ("Crocin 45% (Gardenia jasminoides) (fructus)", "33 mg"),
]
INTRO = ("AngiogenX supports the health of the small blood vessels in your eyes. It combines pine "
         "bark OPCs, trans-resveratrol, curcumin, notoginseng and crocin from gardenia fruit.")
WARNING = ("Avoid use while healing a wound or any other tissue injury, such as a cut, fracture, "
           "sprain or burn. Also avoid use during menses or in pregnancy. Stop a few days before "
           "surgery. Resume once healing is complete. If you take a blood thinner, consult your "
           "physician before use.")
DESCRIPTION = "\n\n".join([
    "Each vegicap supplies 158 mg pine bark OPCs 95%, 100 mg trans-resveratrol 98%, 100 mg "
    "curcumin 95%, 75 mg notoginsenosides 80% and 33 mg crocin 45%. There are no fillers or "
    "excipients.",
    "Other ingredients: Certified Organic Pullulan Vegicap, Healing Energy, Love, and Prayer. "
    "30 vegicaps per bottle.",
    "Caution: " + WARNING,
])
DIRECTIONS = "Take 1 capsule daily with food, or as guided."
PHOTOS = ["/static/product-photos/angiogenx-1.webp",
          "/static/product-photos/angiogenx-2.webp",
          "/static/product-photos/angiogenx-3.webp"]
OLD = ("Mistletoe", "Honokiol", "Wormwood", "Green Tea", "Saffron", "Vitamin D3", "Sumac",
       "EMIQ", "macular degeneration", "retinopathy", "malignanc", "glaucoma")


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_five_ingredients_in_order_and_the_copy(fname):
    e = _entry(fname)
    assert [(i["name"], i["dose"]) for i in e["ingredients"]] == FIVE
    assert e["ingredients_source"] == "label-2026-10-05"
    assert e["description"] == DESCRIPTION
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["bottle_type"] == "30 Caps"


def test_the_store_entry():
    e = _entry()
    assert e["intro"] == INTRO
    assert e["copy_pinned"] == ["ingredients", "intro", "description", "benefits", "research"]
    assert e["content_since"] == "2026-10-08"
    assert e["price_cents"] == 7000 and e["fmp_id"] == "411" and e["name"] == "AngiogenX"
    assert e["benefits"] == [] and e["how_it_works"] == ""
    # Marketing retired the GrooveKart page (404, 2026-10-07), so the flag is cleared.
    for gone in ("gk_stale", "gk_stale_reason", "gk_extra_accepted"):
        assert gone not in e, gone
    text = json.dumps(e)
    for gone in OLD:
        assert gone.lower() not in text.lower(), gone


def test_an_apply_run_cannot_set_the_stale_flag_back():
    # scripts/apply_enrichment.py sets gk_stale only when the clean entry carries it.
    clean = json.load(open(os.path.join(ROOT, "data", "products-enrich-clean.json"),
                           encoding="utf-8"))[SLUG]
    assert "gk_stale" not in clean and "stale_reason" not in clean


def test_the_three_approved_photos_in_order():
    images = _entry()["images"]
    assert [i["src"] for i in images] == PHOTOS
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path
        assert i["alt"].strip(), path


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def test_no_cached_ai_draft_replaces_the_pinned_copy(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.init_table(cx)
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER mistletoe wet AMD")
        # Dated after content_since, so the reset does not remove it: only the pin can.
        cx.execute("UPDATE sales_pages SET generated_at='2099-01-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    dump = json.dumps(d)
    assert "AI DRAFT MARKER" not in dump and "AI HOW MARKER" not in dump
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    for sec in ("intro", "description", "research"):
        assert "ai" not in secs[sec], sec
    assert secs["research"]["body"]["how_it_works"] == ""
    body = secs["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == FIVE
    assert body["directions"] == DIRECTIONS and body["warning"] == WARNING
    assert [i["src"] for i in d["images"]] == PHOTOS


def test_product_data_sends_no_benefits_and_no_how_it_works(appmod):
    d = appmod.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["benefits"] == []
    assert d["how_it_works"] == ""
    assert d["description"] == DESCRIPTION
    assert [i["src"] for i in d["images"]] == PHOTOS
