"""Vitamin D Syntropy: the new formula, back in stock, one capsule a day with a meal.

Spec: production/05 Formulations/_store-updates/2026-10-16-vitamin-d-syntropy.md (copy approved
by Glen 2026-10-17, via formulation). FileMaker 395 holds it. The copy is pinned, benefits and
how-it-works are empty, three approved photos go in the `images` gallery, the page never
queues, generates or shows AI-made images, and the out-of-stock line is gone.

scripts/generate_panels_from_db.py --write and scripts/apply_worklist.py have no pin guard;
this test is what catches either of them rewriting the panel.
"""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "vitamin-d-syntropy"
TWELVE = [
    ("Vitamin A (as Retinyl Acetate)", "750 mcg RAE"),
    ("Vitamin C (as Boron Ascorbate)", "69 mg"),
    ("Vitamin D3 (as Cholecalciferol)", "125 mcg (5000 IU)"),
    ("Vitamin E (as mixed Tocopherols)", "10 mg"),
    ("Vitamin K2 (as all-trans MK-7)", "180 mcg"),
    ("Boron (as Boron Ascorbate)", "6 mg"),
    ("Iodine (as Potassium Iodide)", "150 mcg"),
    ("Magnesium (as Magnesium L-Threonate)", "10.5 mg"),
    ("Selenium (as Methylselenocysteine)", "70 mcg"),
    ("Treasurer of the Gods 20:1 (Butea frondosa)", "90 mg"),
    ("Chlorogenic Acid 20% (Eucommia ulmoides)", "90 mg"),
    ("Xanthohumol 10% (Humulus lupulus)", "18 mg"),
]
INTRO = ("Vitamin D Syntropy supplies 5000 IU of vitamin D3 with the cofactors that help the body "
         "use it. Vitamin K2 as MK-7 helps direct calcium into bone. Magnesium and vitamin A "
         "support vitamin D's activation and its receptor. Boron, iodine, selenium and three "
         "botanicals complete the formula: Butea, Eucommia and hops. Take 1 capsule daily with "
         "a meal, or as guided.")
DIRECTIONS = "Take 1 capsule daily with a meal, or as guided."
WARNING = ("If you take a blood thinner such as warfarin, consult your physician before use, "
           "because vitamin K affects it. Consult your physician before use if you are pregnant "
           "or have a thyroid condition.")
DESCRIPTION = "\n\n".join([
    "Vitamin D is formed in the skin when sunlight transforms cholesterol made by enzymatic "
    "activity in the liver. Vitamin D is not only the vitamin of light, it is also a hormone, "
    "like the other hormones the body makes from cholesterol as a precursor. Vitamin D is "
    "crucial for many functions including calcium metabolism, immunity and nerve function. "
    "Individuals who take more than 2,000 IU per day of vitamin D should have their vitamin D "
    "levels monitored.",
    "Each capsule pairs 5000 IU of D3 with 180 mcg of vitamin K2 as MK-7, vitamin A, vitamin C, "
    "vitamin E, boron, iodine, magnesium as L-threonate and selenium. Three botanicals round it "
    "out: Butea, Eucommia and xanthohumol from hops. The capsule is Certified Organic Pullulan, "
    "with no fillers or excipients.",
    DIRECTIONS,
    "Caution: " + WARNING,
])
PINNED = ["ingredients", "intro", "description", "benefits", "research"]
IMAGES = [
    ("/static/product-photos/vitamin-d-syntropy-1.webp",
     "A woman greeting the morning sun on a Hawaiian beach"),
    ("/static/product-photos/vitamin-d-syntropy-2.webp",
     "Hop cones, flame of the forest flowers, golden powders and a dark amber glass bottle in morning sun"),
    ("/static/product-photos/vitamin-d-syntropy-3.webp",
     "Hop cones glowing on the vine in early sun"),
]
PHOTOS = [src for src, _ in IMAGES]
OLD_FORMULA = ("K1", "MK-4", "Menaquinone-4", "Laminaria", "kelp", "1000 mcg", "Fulvic",
               "Humic", "Spirit Minerals", "Phylloquinone")


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_twelve_ingredients_in_order_and_the_copy(fname):
    e = _entry(fname)
    assert [(i["name"], i["dose"]) for i in e["ingredients"]] == TWELVE
    assert e["ingredients_source"] == "label-2026-10-16"
    assert e["intro"] == INTRO
    assert e["description"] == DESCRIPTION
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["bottle_type"] == "30 Caps"
    assert "gk_stale" not in e
    dump = json.dumps(e)
    for word in OLD_FORMULA:
        assert word not in dump, word


def test_the_store_entry():
    e = _entry()
    assert e["copy_pinned"] == PINNED
    assert e["benefits"] == [] and e["how_it_works"] == ""
    assert "out_of_stock" not in e
    assert "enrichment_note" not in e
    assert "inactive" not in e and "waitlist_only" not in e
    assert e["content_since"] == "2026-10-19"
    assert e["price_cents"] == 7000 and e["fmp_id"] == "395" and e["name"] == "Vitamin D Syntropy"
    assert e["pinecone_title"] == "Vitamin D Syntropy"
    assert e["qty_pricing"] is True


def test_the_slug_map_resolves_filemaker_395():
    m = json.load(open(os.path.join(ROOT, "data", "fmp_slug_map.json"), encoding="utf-8"))
    assert m["resolved"]["395"] == SLUG


def test_the_three_approved_photos_in_order_with_their_alt_text():
    images = _entry()["images"]
    assert [(i["src"], i["alt"]) for i in images] == IMAGES
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path


def test_the_worklist_no_longer_appends_to_it():
    from scripts import apply_worklist as w
    assert SLUG not in w.SECTION_A


def test_the_slug_is_in_no_ai_images():
    from dashboard import sales_images as si
    assert SLUG in si.NO_AI_IMAGES


def test_the_queue_refuses_the_slug(tmp_path):
    from dashboard import sales_images as si
    with sqlite3.connect(tmp_path / "q.db") as cx:
        assert si.enqueue(cx, SLUG) is False
        assert si.queue_state(cx, SLUG) is None
        cx.execute("INSERT INTO sales_image_queue (product_slug, state, requested_at, updated_at) "
                   "VALUES (?, 'pending', '1', '1')", (SLUG,))
        assert si.enqueue(cx, "angiogenx") is True
        assert si.list_pending(cx) == ["angiogenx"]


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
    monkeypatch.setattr(a, "_SALES_AI_IMAGES_ENABLED", True)
    return a


def test_no_cached_ai_draft_replaces_the_pinned_copy(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.init_table(cx)
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER with K1 and kelp")
        # Dated after content_since, so the reset does not remove it: only the pin can.
        cx.execute("UPDATE sales_pages SET generated_at='2099-01-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    dump = json.dumps(d)
    assert "AI DRAFT MARKER" not in dump and "AI HOW MARKER" not in dump
    assert "out_of_stock_line" not in d and d["price_cents"] == 7000
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    for sec in ("intro", "description", "research"):
        assert "ai" not in secs[sec], sec
    assert secs["research"]["body"]["how_it_works"] == ""
    body = secs["ingredients"]["body"]
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == TWELVE
    assert body["directions"] == DIRECTIONS and body["warning"] == WARNING
    assert [(i["src"], i["alt"]) for i in d["images"]] == IMAGES


@pytest.mark.parametrize("variations", [False, True])
def test_the_images_section_never_says_generating(appmod, monkeypatch, variations):
    monkeypatch.setattr(appmod, "_SALES_IMAGE_VARIATIONS_ENABLED", variations)
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    assert "images" not in [s["id"] for s in d["sections"]]
    # The control: another product on the same app does get a state, so the check can fail.
    d2 = appmod.app.test_client().get("/begin/product-page-data/angiogenx").get_json()
    img2 = next(s for s in d2["sections"] if s["id"] == "images")
    assert "state" in img2["body"]


def test_the_image_gen_route_refuses_the_slug(appmod):
    c = appmod.app.test_client()
    assert c.post(f"/begin/product-image-gen/{SLUG}").status_code == 404


def test_product_data_is_on_sale_with_no_benefits_and_no_how_it_works(appmod):
    d = appmod.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["benefits"] == []
    assert d["how_it_works"] == ""
    assert d["description"] == DESCRIPTION
    assert d["price_cents"] == 7000
    assert d["formats"] is not None
    assert [i["src"] for i in d["images"]] == PHOTOS


def test_the_buy_page_no_longer_redirects(appmod):
    r = appmod.app.test_client().get(f"/begin/buy/{SLUG}")
    assert r.status_code == 200 and "Location" not in r.headers


def test_the_learn_page_serves_the_description(appmod):
    d = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == DESCRIPTION
