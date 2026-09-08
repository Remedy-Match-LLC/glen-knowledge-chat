import importlib
import sqlite3
import pathlib
import pytest
from dashboard import sales_images as si
from dashboard import sales_image_prompts as sip
from dashboard import replicate_client as rc


def _reload(monkeypatch, tmp_path, imgs="true"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_IMAGES", imgs)
    import app as appmod; importlib.reload(appmod); return appmod

def _cx(): return sqlite3.connect(":memory:")

class _Resp:
    def __init__(self, js=None, content=b"", status=200): self._js=js; self.content=content; self.status_code=status
    def json(self): return self._js
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("http %d" % self.status_code)

def test_queue_enqueue_pending_done():
    cx = _cx()
    si.enqueue(cx, "longevity")
    assert si.list_pending(cx) == ["longevity"]
    assert si.queue_state(cx, "longevity") == "pending"
    si.mark_done(cx, "longevity")
    assert si.list_pending(cx) == []
    assert si.queue_state(cx, "longevity") == "done"

def test_enqueue_idempotent_resets_to_pending():
    cx = _cx()
    si.enqueue(cx, "energy"); si.mark_failed(cx, "energy")
    si.enqueue(cx, "energy")
    assert si.queue_state(cx, "energy") == "pending"

def test_record_and_display_first_ready_per_kind():
    cx = _cx()
    si.record_image(cx, "longevity", "botanical", 1, "botanical-1.png")
    si.record_image(cx, "longevity", "botanical", 2, "botanical-2.png")
    si.record_image(cx, "longevity", "mechanism", 1, "mechanism-1.png")
    disp = si.display_images(cx, "longevity")
    assert disp == {"botanical": "botanical-1.png", "mechanism": "mechanism-1.png"}
    assert len(si.get_images(cx, "longevity")) == 3

def test_prompts_two_modes_one_image_each():
    # Glen retired the image vote on 2026-09-08, so the second of each pair was being
    # generated and never shown. One prompt per kind, two images per product.
    p = sip.build_image_prompts({"name": "Longevity", "ingredients": [{"name": "Resveratrol"}]})
    assert set(p.keys()) == {"botanical", "mechanism"}
    assert len(p["botanical"]) == 1 and len(p["mechanism"]) == 1

def test_prompts_always_forbid_text_and_packaging():
    # The exclusion is what keeps text out of the image, NOT the absence of ingredient
    # names. Measured 2026-09-08: naming an ingredient renders the ingredient. What made
    # Flux render garbled text was asking for labels, and PR #174 removed that.
    p = sip.build_image_prompts({"name": "Longevity", "ingredients": [{"name": "Resveratrol"}, "Quercetin"]})
    for prompt in p["botanical"] + p["mechanism"]:
        low = prompt.lower()
        assert "no text" in low and "no labels" in low
        assert "bottles" in low  # the no-packaging exclusion names bottles explicitly


def test_falls_back_to_generic_when_no_model_configured():
    # derive_scenes needs an injected client. Without one every product still gets a
    # usable scene rather than no image at all.
    sip.configure(client=None)
    assert sip.derive_scenes({"name": "X", "ingredients": [{"name": "Resveratrol"}]}) is None
    p = sip.build_image_prompts({"name": "X", "ingredients": [{"name": "Resveratrol"}]})
    assert "kitchen" in p["botanical"][0].lower()
    assert "cell" in p["mechanism"][0].lower() or "field" in p["mechanism"][0].lower()


def test_scene_is_derived_from_the_products_own_ingredients():
    class _Blk:
        text = '{"botanical": "crimson saffron threads and deep purple-black rice grains beside halved '\
               'lemons on a wooden counter, a mature woman arranging them, herb garden behind",' \
               ' "mechanism": "a luminous human eye, its retina a lattice of flexible lipid membrane, '\
               'golden droplets flowing into the retinal layers"}'
    class _Msg:
        content = [_Blk()]
    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                _Client.seen = kw
                return _Msg()
    sip.configure(client=_Client)
    try:
        p = sip.build_image_prompts({"name": "WholOmega",
                                     "ingredients": [{"name": "DHA from Whole Algae Oil"},
                                                     {"name": "Safranal (Crocus sativa)"}]})
        # the formula's own ingredients reached the model
        assert "Safranal" in str(_Client.seen)
        # and its own botanicals reached the image prompt
        assert "saffron" in p["botanical"][0].lower()
        assert "retina" in p["mechanism"][0].lower()
        # the exclusion survives scene substitution
        assert "no text" in p["botanical"][0].lower()
    finally:
        sip.configure(client=None)

def test_generate_image_returns_bytes(monkeypatch):
    calls = {"post": 0, "get": 0}
    def fake_post(url, **kw):
        calls["post"] += 1
        return _Resp(js={"status": "succeeded", "output": "https://img/x.png", "urls": {"get": "https://api/get"}})
    def fake_get(url, **kw):
        calls["get"] += 1
        return _Resp(content=b"PNGBYTES")
    monkeypatch.setattr(rc.requests, "post", fake_post)
    monkeypatch.setattr(rc.requests, "get", fake_get)
    out = rc.generate_image("a prompt", token="tok")
    assert out == b"PNGBYTES" and calls["post"] == 1

def test_generate_image_raises_on_failed_status(monkeypatch):
    monkeypatch.setattr(rc.requests, "post", lambda url, **kw: _Resp(js={"status": "failed", "urls": {"get": "g"}}))
    with pytest.raises(Exception):
        rc.generate_image("p", token="tok")

def test_generate_image_requires_token(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with pytest.raises(Exception):
        rc.generate_image("p")


def test_worker_generates_and_records(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"ingredients": [{"name": "Resveratrol"}]})
    from dashboard import replicate_client as rc
    monkeypatch.setattr(rc, "generate_image", lambda prompt, **kw: b"PNG")
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx: si.enqueue(cx, slug)
    appmod._drain_sales_image_queue()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert si.queue_state(cx, slug) == "done"
        # Two, one per kind. It was four while the page asked buyers to vote between
        # pairs; Glen retired that vote on 2026-09-08.
        assert len(si.get_images(cx, slug)) == 2
    files = list((appmod._SALES_IMG_DIR / slug).glob("*.png"))
    assert len(files) == 2
    assert sorted(f.name for f in files) == ["botanical-1.png", "mechanism-1.png"]


def test_worker_flag_off_noop(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path, imgs="false")
    assert appmod._SALES_AI_IMAGES_ENABLED is False
    from dashboard import sales_images as si
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    with sqlite3.connect(appmod.LOG_DB) as cx: si.enqueue(cx, slug)
    appmod._drain_sales_image_queue()  # flag off → no-op
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert si.queue_state(cx, slug) == "pending"


def test_worker_marks_failed_on_error(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    monkeypatch.setattr(appmod, "_product_card", lambda p: {"ingredients": []})
    from dashboard import replicate_client as rc
    def boom(prompt, **kw): raise RuntimeError("replicate down")
    monkeypatch.setattr(rc, "generate_image", boom)
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx: si.enqueue(cx, slug)
    appmod._drain_sales_image_queue()
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert si.queue_state(cx, slug) == "failed"


def test_enqueue_route_and_404_when_flag_off(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    c = appmod.app.test_client()
    r = c.post(f"/begin/product-image-gen/{slug}")
    assert r.status_code == 200 and r.get_json().get("ok") is True
    from dashboard import sales_images as si
    import sqlite3
    with sqlite3.connect(appmod.LOG_DB) as cx: assert si.queue_state(cx, slug) == "pending"
    # flag off
    off = _reload(monkeypatch, tmp_path, imgs="false")
    assert off.app.test_client().post(f"/begin/product-image-gen/{slug}").status_code == 404

def test_serve_image_route(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    d = appmod._SALES_IMG_DIR / slug; d.mkdir(parents=True, exist_ok=True)
    (d / "botanical-1.png").write_bytes(b"\x89PNG\r\n")
    c = appmod.app.test_client()
    assert c.get(f"/begin/product-image/{slug}/botanical-1.png").status_code == 200
    assert c.get(f"/begin/product-image/{slug}/missing.png").status_code == 404
    assert c.get(f"/begin/product-image/{slug}/../evil.png").status_code in (400, 404)


def test_page_data_images_states(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    c = appmod.app.test_client()
    # none
    d = c.get(f"/begin/product-page-data/{slug}").get_json()
    img = next(s for s in d["sections"] if s["id"] == "images")["body"]
    assert img.get("state") == "none"
    # generating
    import sqlite3
    from dashboard import sales_images as si
    with sqlite3.connect(appmod.LOG_DB) as cx: si.enqueue(cx, slug)
    img = next(s for s in c.get(f"/begin/product-page-data/{slug}").get_json()["sections"] if s["id"]=="images")["body"]
    assert img.get("state") == "generating"
    # ready
    with sqlite3.connect(appmod.LOG_DB) as cx:
        si.record_image(cx, slug, "botanical", 1, "botanical-1.png")
        si.record_image(cx, slug, "mechanism", 1, "mechanism-1.png")
    img = next(s for s in c.get(f"/begin/product-page-data/{slug}").get_json()["sections"] if s["id"]=="images")["body"]
    assert img.get("state") == "ready"
    urls = [i["url"] for i in img["images"]]
    assert f"/begin/product-image/{slug}/botanical-1.png" in urls

def test_page_data_images_flag_off(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path, imgs="false")
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    img = next(s for s in appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()["sections"] if s["id"]=="images")["body"]
    assert "state" not in img

def test_flag_defaults_off(monkeypatch, tmp_path):
    monkeypatch.delenv("SALES_PAGES_AI_IMAGES", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    import importlib, app as appmod; importlib.reload(appmod)
    assert appmod._SALES_AI_IMAGES_ENABLED is False
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    assert appmod.app.test_client().post(f"/begin/product-image-gen/{slug}").status_code == 404


def test_enqueue_route_skips_reenqueue_when_images_exist(monkeypatch, tmp_path):
    """Enqueue route must return state='done' and NOT reset the queue to pending
    when ready images already exist on disk (cost/abuse guard regression test)."""
    appmod = _reload(monkeypatch, tmp_path)
    slug = next(iter(appmod._PRODUCTS["products"].keys()))
    from dashboard import sales_images as si
    import sqlite3
    # Record both image kinds so display_images returns non-None values
    with sqlite3.connect(appmod.LOG_DB) as cx:
        si.record_image(cx, slug, "botanical", 1, "botanical-1.png")
        si.record_image(cx, slug, "mechanism", 1, "mechanism-1.png")
    # POST the enqueue route — should short-circuit without creating a pending row
    c = appmod.app.test_client()
    r = c.post(f"/begin/product-image-gen/{slug}")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("ok") is True
    assert data.get("state") == "done"
    # The queue row must NOT be pending (either None or some other state)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert si.queue_state(cx, slug) != "pending"


def test_a_single_ingredient_product_uses_its_own_name():
    # 12 of 30 sampled products carry no ingredient list. Where the product IS the
    # ingredient (serrapeptase, asiaticosides), its name is enough to build a scene.
    class _Client:
        seen = None
        class messages:
            @staticmethod
            def create(**kw):
                _Client.seen = kw
                raise RuntimeError("stop here, we only care what was asked")
    sip.configure(client=_Client)
    try:
        sip.derive_scenes({"name": "Serrapeptase", "ingredients": []})
        assert "Serrapeptase" in str(_Client.seen)
        # a product with neither name nor ingredients still cannot be described
        _Client.seen = None
        assert sip.derive_scenes({"name": "", "ingredients": []}) is None
        assert _Client.seen is None
    finally:
        sip.configure(client=None)


def test_the_brief_asks_to_keep_her_face_in_frame():
    # Two of the first three botanical images cropped her head out. Glen, 2026-09-08:
    # "face in scene is good".
    class _Client:
        seen = None
        class messages:
            @staticmethod
            def create(**kw):
                _Client.seen = kw
                raise RuntimeError("only the brief matters here")
    sip.configure(client=_Client)
    try:
        sip.derive_scenes({"name": "X", "ingredients": [{"name": "Turmeric"}]})
        brief = str(_Client.seen).lower()
        assert "face visible" in brief
        assert "crop her head" in brief
    finally:
        sip.configure(client=None)
