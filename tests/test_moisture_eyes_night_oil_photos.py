"""Moisture-Eyes Night Oil: four approved photos, no AI images, and the 15 mL bottle.

Glen said yes in the sales tab on 2026-10-30, and to the write in the platform tab. The real
bottle photo goes first (sales/assets/2026-10-30-moisture-eyes-night-oil/bottle-photo.jpg),
then label options 1 to 3 (production/05 Formulations/moisture-eyes-night-oil/2026-10-31/
label-images). The alts name no plant: the leaves are not true sea buckthorn and the trees
are not clearly kukui (USE-NOTES.md beside the images). The new label is 15 mL.
"""
import importlib
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "moisture-eyes-night-oil"
IMAGES = [
    ("/static/product-photos/moisture-eyes-night-oil-1.webp",
     "A 15 mL bottle of Moisture-Eyes Night Oil held in a hand"),
    ("/static/product-photos/moisture-eyes-night-oil-2.webp",
     "Orange berries, cracked nuts and a dish of golden oil under a night sky"),
    ("/static/product-photos/moisture-eyes-night-oil-3.webp",
     "A woman resting with her eyes closed in soft evening light"),
    ("/static/product-photos/moisture-eyes-night-oil-4.webp",
     "Moonlight on the ocean, framed by leafy trees"),
]


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


def test_the_four_photos_in_order_with_their_alt_text():
    images = _entry()["images"]
    assert [(i["src"], i["alt"]) for i in images] == IMAGES
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path


def test_each_file_is_its_own_photo():
    """Review round 2: names and alts alone pass with swapped or repeated files. The bottle is
    the only portrait photo; the three label options are 1344 by 768, and all four differ."""
    import hashlib
    from PIL import Image
    paths = [os.path.join(ROOT, src.lstrip("/")) for src, _ in IMAGES]
    assert [Image.open(p).size for p in paths] == [(1200, 1600)] + [(1344, 768)] * 3
    assert len({hashlib.sha256(open(p, "rb").read()).hexdigest() for p in paths}) == 4


def test_no_alt_names_a_plant():
    for _, alt in IMAGES:
        for word in ("buckthorn", "kukui", "hippophae", "aleurites", "emu"):
            assert word not in alt.lower(), alt


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_description_says_15_ml(fname):
    desc = _entry(fname)["description"]
    assert desc.endswith(" 15 mL per bottle.")
    assert " 5 mL" not in desc


def test_the_slug_is_in_no_ai_images(tmp_path):
    from dashboard import sales_images as si
    assert SLUG in si.NO_AI_IMAGES
    with sqlite3.connect(tmp_path / "q.db") as cx:
        assert si.enqueue(cx, SLUG) is False


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
    monkeypatch.setattr(a, "_SALES_AI_IMAGES_ENABLED", True)
    return a


def test_the_page_shows_the_photos_and_no_ai_images_section(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    assert [(i["src"], i["alt"]) for i in d["images"]] == IMAGES
    assert "images" not in [s["id"] for s in d["sections"]]
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["description"]["body"].endswith("15 mL per bottle.")


def test_the_image_gen_route_refuses_the_slug(appmod):
    assert appmod.app.test_client().post(
        f"/begin/product-image-gen/{SLUG}").status_code == 404
