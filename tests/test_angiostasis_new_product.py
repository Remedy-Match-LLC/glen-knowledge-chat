"""Angiostasis: a new six-ingredient formula, one capsule a day with the evening meal.

Spec: production/05 Formulations/_store-updates/2026-10-08-angiostasis.md (Glen "approve",
2026-10-08, via formulation). FileMaker 1201 holds it. The copy is pinned, benefits and
how-it-works are empty, the two approved photos go in the `images` gallery, and the page
never queues, generates or shows AI-made images (Glen ruled out a blood-vessel picture).
"""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "angiostasis"
SIX = [
    ("Green Tea EGCG 50% (Camellia sinensis)", "150 mg"),
    ("Silymarin 80% (Silybum marianum) (fructus)", "125 mg"),
    ("Curcumin (Curcuma longa) (rhizoma)", "100 mg"),
    ("Pterostilbene (Pterocarpus marsupium)", "50 mg"),
    ("Honokiol (Magnolia grandiflora) (cortex)", "50 mg"),
    ("Selenium (as Methylselenocysteine)", "205 mcg"),
]
INTRO = ("Angiostasis supports healthy, orderly blood vessel growth throughout the body. Its "
         "formula combines green tea, milk thistle, curcumin, pterostilbene, honokiol and selenium.")
DIRECTIONS = "Take 1 capsule daily with the evening meal, or as guided."
WARNING = ("May cause drowsiness. Avoid use while healing a wound or any other tissue injury, such "
           "as a cut, fracture, sprain or burn. Also avoid use during menses, in pregnancy or while "
           "breastfeeding. Stop a few days before surgery. Resume once healing is complete. If you "
           "take a blood thinner, consult your physician before use. If you are under medical "
           "treatment, consult your physician before use.")
DESCRIPTION = "\n\n".join([
    "Each vegicap supplies 150 mg green tea extract (EGCG 50%), 125 mg silymarin 80%, 100 mg "
    "curcumin, 50 mg pterostilbene, 50 mg honokiol and 205 mcg selenium as methylselenocysteine. "
    "There are no fillers or excipients.",
    DIRECTIONS,
    "Angiostasis is synergistic with Brain Cleanse, which is taken at bedtime.",
    "Other ingredients: Certified Organic Pullulan Vegicap, Healing Energy, Love, and Prayer. "
    "30 vegicaps per bottle.",
    "Caution: " + WARNING,
])
NOTE = "Synergistic with Brain Cleanse."
LINK = {"label": "Brain Cleanse", "url": "/begin/product/brain-cleanse"}
PHOTOS = ["/static/product-photos/angiostasis-1.webp",
          "/static/product-photos/angiostasis-2.webp"]


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_six_ingredients_in_order_and_the_copy(fname):
    e = _entry(fname)
    assert [(i["name"], i["dose"]) for i in e["ingredients"]] == SIX
    assert e["ingredients_source"] == "label-2026-10-08"
    assert e["description"] == DESCRIPTION
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["panel_note"] == NOTE
    assert e["panel_note_link"] == LINK
    assert e["bottle_type"] == "30 Caps"


def test_the_store_entry():
    e = _entry()
    assert e["intro"] == INTRO
    assert e["copy_pinned"] == ["ingredients", "intro", "description", "benefits", "research"]
    assert e["content_since"] == "2026-10-08"
    assert e["price_cents"] == 7000 and e["fmp_id"] == "1201" and e["name"] == "Angiostasis"
    assert e["pinecone_title"] == "Angiostasis"
    assert e["url"] == "https://myhealingoasis.com/begin/product/angiostasis"
    assert e["qty_pricing"] is True and e["no_groovekart"] is True
    assert e["benefits"] == [] and e["how_it_works"] == ""
    assert "qbo_item_id" not in e


def test_the_slug_map_resolves_filemaker_1201():
    m = json.load(open(os.path.join(ROOT, "data", "fmp_slug_map.json"), encoding="utf-8"))
    assert m["resolved"]["1201"] == SLUG


def test_the_two_approved_photos_in_order():
    images = _entry()["images"]
    assert [i["src"] for i in images] == PHOTOS
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path
        assert i["alt"].strip(), path
    assert "Milk thistle flowers" in images[0]["alt"]
    assert "kitchen" in images[1]["alt"]


def test_the_queue_refuses_the_slug(tmp_path):
    from dashboard import sales_images as si
    with sqlite3.connect(tmp_path / "q.db") as cx:
        assert si.enqueue(cx, SLUG) is False
        assert si.queue_state(cx, SLUG) is None
        # A row written before the opt-out, or by any other path, is never drained.
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
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER shrinks tumours")
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
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == SIX
    assert body["directions"] == DIRECTIONS and body["warning"] == WARNING
    assert [i["src"] for i in d["images"]] == PHOTOS


@pytest.mark.parametrize("variations", [False, True])
def test_the_images_section_never_says_generating(appmod, monkeypatch, variations):
    # Any `state` other than "ready" leaves the page on "Generating" and posting to image-gen.
    monkeypatch.setattr(appmod, "_SALES_IMAGE_VARIATIONS_ENABLED", variations)
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    img = next(s for s in d["sections"] if s["id"] == "images")
    assert img["body"] == {"images": []}, img["body"]
    # The control: another product on the same app does get a state, so the check can fail.
    d2 = appmod.app.test_client().get("/begin/product-page-data/angiogenx").get_json()
    img2 = next(s for s in d2["sections"] if s["id"] == "images")
    assert "state" in img2["body"]


def test_the_image_gen_route_refuses_the_slug(appmod):
    c = appmod.app.test_client()
    assert c.post(f"/begin/product-image-gen/{SLUG}").status_code == 404
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert si.queue_state(cx, SLUG) is None


def test_product_data_sends_no_benefits_and_no_how_it_works(appmod):
    d = appmod.app.test_client().get(f"/begin/product-data/{SLUG}").get_json()
    assert d["benefits"] == []
    assert d["how_it_works"] == ""
    assert d["description"] == DESCRIPTION
    assert d["price_cents"] == 7000
    assert d["formats"] is not None
    assert [i["src"] for i in d["images"]] == PHOTOS


def test_stored_ai_images_are_never_offered(appmod, monkeypatch):
    # Rows written by any path stay off the page, in every image mode.
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for kind in ("botanical", "mechanism"):
            for v in (1, 2):
                si.record_image(cx, SLUG, kind, v, f"{kind}-{v}.png")
    c = appmod.app.test_client()
    for variations, pick in ((False, False), (False, True), (True, False)):
        monkeypatch.setattr(appmod, "_SALES_IMAGE_VARIATIONS_ENABLED", variations)
        monkeypatch.setattr(appmod, "_SALES_IMAGE_PICK_ENABLED", pick)
        d = c.get(f"/begin/product-page-data/{SLUG}").get_json()
        assert f"/begin/product-image/{SLUG}/" not in json.dumps(d), (variations, pick)


def test_the_image_serve_pick_and_vote_routes_refuse_the_slug(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_SALES_IMAGE_PICK_ENABLED", True)
    monkeypatch.setattr(appmod, "_SALES_IMAGE_VOTE_ENABLED", True)
    d = appmod._SALES_IMG_DIR / SLUG
    d.mkdir(parents=True, exist_ok=True)
    (d / "mechanism-1.png").write_bytes(b"\x89PNG fake")
    c = appmod.app.test_client()
    assert c.get(f"/begin/product-image/{SLUG}/mechanism-1.png").status_code == 404
    body = {"kind": "mechanism", "variant": 1}
    assert c.post(f"/begin/product-image-pick/{SLUG}", json=body).status_code == 404
    assert c.post(f"/begin/product-image-vote/{SLUG}", json=body).status_code == 404
    # The control: the same calls for another product are not refused.
    assert c.post("/begin/product-image-pick/angiogenx", json=body).status_code == 200


def test_the_tournament_never_renders_for_the_slug(appmod, monkeypatch):
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx:
        si.record_image(cx, SLUG, "mechanism", 1, "mechanism-1.png")
    monkeypatch.setattr(appmod, "_SALES_IMAGE_TOURNAMENT_ENABLED", True)
    seen = []
    monkeypatch.setattr(appmod, "_render_challenger", lambda slug, kind, p: seen.append(slug))
    from dashboard import sales_image_pairs as sp
    monkeypatch.setattr(sp, "ensure_pair", lambda cx, slug, kind, vs: (seen.append(slug), None)[1])
    appmod._run_image_tournament()
    assert SLUG not in seen
