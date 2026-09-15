"""Real product photos shown uncropped on the product page, 2026-09-14.

Glen approved three Nous Energy sales photos and chose to "lead with the label hero image".
The page had one square hero, which would cut the sides off these 1344x768 photos, so the
catalog gains an optional "images" list and the page renders it as a gallery.
"""
import json
import os

import pytest

import app as appmod

PHOTOS = ["/static/product-photos/nous-energy-1.webp",
          "/static/product-photos/nous-energy-2.webp",
          "/static/product-photos/nous-energy-3.webp"]


def test_nous_energy_lists_the_three_approved_photos_in_order():
    images = json.load(open("data/products.json"))["products"]["nous-energy"]["images"]
    assert [i["src"] for i in images] == PHOTOS
    for i in images:
        path = i["src"].lstrip("/")
        assert os.path.getsize(path) < 250_000, path
        assert open(path, "rb").read(12)[8:12] == b"WEBP", path
        assert i["alt"].strip(), path


def _product(images):
    return {"slug": "photo-test", "name": "Photo Test", "price_cents": 4000,
            "description": "A test product.", "qbo_item_id": "", "images": images}


@pytest.mark.parametrize("route", ["/begin/product-data/", "/begin/product-page-data/"])
def test_both_product_routes_send_only_src_and_alt(monkeypatch, route):
    images = [{"src": "/static/a.webp", "alt": "First", "internal_note": "never public"},
              {"alt": "no src, dropped"},
              "not a dict",
              {"src": "/static/b.webp"}]
    monkeypatch.setattr(appmod, "_get_product", lambda s: _product(images) if s == "photo-test" else None)
    r = appmod.app.test_client().get(route + "photo-test")
    assert r.status_code == 200
    assert r.get_json()["images"] == [{"src": "/static/a.webp", "alt": "First"},
                                      {"src": "/static/b.webp", "alt": ""}]


@pytest.mark.parametrize("route", ["/begin/product-data/", "/begin/product-page-data/"])
def test_a_product_without_photos_gets_an_empty_list(monkeypatch, route):
    p = _product(None)
    p.pop("images")
    monkeypatch.setattr(appmod, "_get_product", lambda s: p if s == "photo-test" else None)
    r = appmod.app.test_client().get(route + "photo-test")
    assert r.status_code == 200
    assert r.get_json()["images"] == []


def test_the_page_renders_the_gallery_uncropped():
    html = open("static/begin-product.html").read()
    assert 'id="sp-gallery"' in html
    assert "data.images.forEach" in html
    gallery_css = html.split(".p-gallery img{", 1)[1].split("}", 1)[0]
    assert "aspect-ratio:1344/768" in gallery_css
    assert "object-fit:cover" not in gallery_css
