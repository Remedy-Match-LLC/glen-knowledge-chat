"""Clear the Way: the October 2026 batch, serrapeptase 500 mg in DRcaps, one capsule a day.

Spec: production/05 Formulations/_store-updates/2026-10-08-clear-the-way.md (Glen "approve",
2026-10-09, via formulation). FileMaker 1125. The copy is pinned, benefits and how-it-works are
empty, the two approved photos go in the `images` gallery, the capsule paragraph is the DRcaps
one, and the page never queues, generates or shows AI-made images.
Modelled on tests/test_angiostasis_new_product.py.
"""
import importlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "clear-the-way"
ONE = [{"name": "Serrapeptase 400,000 u/g (Serratiopeptidase) (Serratia marcescens)",
        "dose": "500 mg", "compound_mg": 500.0}]
INTRO = ("Clear the Way supplies serrapeptase, an enzyme first found in a bacterium from the "
         "silkworm's gut. It supports the body's natural clearance of fibrin and other non-living "
         "proteins, so that fluids and circulation can flow freely.")
DIRECTIONS = "Take 1 capsule daily on an empty stomach, between meals, or as guided."
WARNING = "If you take a blood thinner, consult your physician before use."
DESCRIPTION = "\n\n".join([
    "Each capsule supplies 500 mg serrapeptase at 400,000 units per gram, about 200,000 units. "
    "There are no fillers or excipients.",
    DIRECTIONS,
    "Other ingredients: Phthalate-free DRcaps\u2122 delayed-release vegicap (hypromellose, gellan "
    "gum), Healing Energy, Love, and Prayer. 30 vegicaps per bottle.",
    "Caution: " + WARNING,
])
NOTE = ("October 2026 batch CTW-20261008-10, serrapeptase 500 mg in DRcaps, one capsule a day "
        "(Glen, via formulation). Spec: production/05 Formulations/_store-updates/"
        "2026-10-08-clear-the-way.md.")
PHOTOS = ["/static/product-photos/clear-the-way-1.webp",
          "/static/product-photos/clear-the-way-2.webp"]
ALTS = ["White silkworm cocoons on green leaves in a woven basket, beside a skein of raw silk",
        "A clear stream running over rounded stones through tree ferns"]


def _entry(fname="products.json"):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    return doc.get("products", doc)[SLUG]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_ingredient_line_and_the_copy(fname):
    e = _entry(fname)
    assert e["ingredients"] == ONE
    assert e["ingredients_source"] == "label-2026-10-08"
    assert e["description"] == DESCRIPTION
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["bottle_type"] == "30 Caps"


def test_the_note_is_in_the_corrections_file_only():
    assert _entry("products-manual-corrections.json")["note"] == NOTE
    assert "note" not in _entry()


def test_the_store_entry():
    e = _entry()
    assert e["intro"] == INTRO
    assert e["copy_pinned"] == ["ingredients", "intro", "description", "benefits", "research"]
    assert e["price_cents"] == 7000 and e["fmp_id"] == "1125" and e["name"] == "Clear the Way"
    assert e["pinecone_title"] == "Clear the Way"
    assert e["url"] == "https://myhealingoasis.com/begin/product/clear-the-way"
    assert e["legacy_store_url"] == "https://remedymatch.com/remedies/syntropy/20-clear-the-way"
    assert e["qty_pricing"] is True
    assert e["benefits"] == [] and e["how_it_works"] == ""


def test_content_since_is_a_past_utc_time_after_the_batch():
    # A future value regenerates cached text on every view until it passes (spec, round 3).
    since = _entry()["content_since"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", since), since
    assert "2026-10-09T" <= since <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_capsule_paragraph_is_drcaps():
    from dashboard import capsule_copy as cc
    assert cc.capsule_kind(SLUG, _entry()) == "drcaps"
    assert cc.capsule_for(SLUG, _entry()) == cc.CAPSULE_COPY["drcaps"]


def test_the_two_approved_photos_in_order():
    images = _entry()["images"]
    assert [i["src"] for i in images] == PHOTOS
    assert [i["alt"] for i in images] == ALTS
    for i in images:
        path = os.path.join(ROOT, i["src"].lstrip("/"))
        assert os.path.getsize(path) <= 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path


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
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER dissolves scar tissue")
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
    assert [(i["name"], i["dose"]) for i in body["ingredients"]] == [(ONE[0]["name"], ONE[0]["dose"])]
    assert body["directions"] == DIRECTIONS and body["warning"] == WARNING
    from dashboard.capsule_copy import CAPSULE_COPY
    assert body["capsule"] == CAPSULE_COPY["drcaps"]
    assert [i["src"] for i in d["images"]] == PHOTOS


@pytest.mark.parametrize("variations", [False, True])
def test_the_images_section_never_says_generating(appmod, monkeypatch, variations):
    # Any `state` other than "ready" leaves the page on "Generating" and posting to image-gen.
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
    other = appmod._SALES_IMG_DIR / "angiogenx"
    other.mkdir(parents=True, exist_ok=True)
    (other / "mechanism-1.png").write_bytes(b"\x89PNG fake")
    assert c.get("/begin/product-image/angiogenx/mechanism-1.png").status_code == 200
    assert c.post("/begin/product-image-pick/angiogenx", json=body).status_code == 200
    assert c.post("/begin/product-image-vote/angiogenx", json=body).status_code == 200


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


def test_the_learn_page_serves_the_description(appmod):
    d = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}").get_json()
    assert d["markdown"] == DESCRIPTION


ATLAS_ID = "5-ways-quot-clear-the-way-quot-naturally-transforms-tissue-h"


def _atlas(fname):
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("id") in (ATLAS_ID, "serrapeptase") and "label" in o:
                found[o["id"]] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8")))
    return found


@pytest.mark.parametrize("fname", ["atlas-concepts.json", "atlas-seed-input.json"])
def test_the_atlas_concepts(fname):
    # Both files: atlas_build.py rebuilds the concepts from the seed.
    c = _atlas(fname)
    ctw, serra = c[ATLAS_ID], c["serrapeptase"]
    assert ctw["label"] == "Clear the Way"
    assert ctw["summary"] == INTRO
    assert "scar tissue formula" not in ctw["aliases"] and "clear the way" in ctw["aliases"]
    assert any(l["type"] == "product" and l["url"].endswith("/begin/product/clear-the-way")
               for l in ctw["links"])
    assert not any(l.get("type") == "product" for l in serra.get("links", []))
