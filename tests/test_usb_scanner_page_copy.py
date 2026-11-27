"""Marketing's USB scanner page text, Glen 2026-11-26 platform tab:
"yes, publish marketing's USB page text"."""
import pathlib
import re

import app

SLUG = "bioenergetic-wellness-scanner-usb"


def _rec():
    return app._PRODUCTS["products"][SLUG]


def test_name_and_text():
    r = _rec()
    assert r["name"] == "Energy4Life (E4L) Bioenergetic Wellness Scanner, USB"
    assert r["intro"].startswith("The original wired USB Energy4Life (E4L)")
    assert set(r["copy_pinned"]) >= {"intro", "description"}
    paras = r["description"].split("\n\n")
    assert len(paras) == 10
    assert paras[6] == ("See the [Bluetooth version](/begin/product/"
                        "bioenergetic-wellness-scanner-bluetooth) on its own page.")


def test_old_offers_are_gone():
    t = (_rec()["intro"] + _rec()["description"]).lower()
    for w in ("$297", "guarantee", "mihealth", "pocket doctor", "actual healing",
              "discovery", "nes "):
        assert w not in t, w
    assert "—" not in t


def test_old_name_still_finds_the_page():
    assert app._get_product("bioenergetic-wellness-scanner-usb")["slug"] == SLUG
    assert "Bioenergetic Wellness Scanner - USB" in _rec()["aliases"]


def test_page_links_only_same_site_paths():
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" /
           "begin-product.html").read_text()
    fn = src[src.index("function renderDescriptionBody"):]
    fn = fn[:fn.index("function renderVideoBody")]
    assert "keepBreaks !== true" in fn and "textContent = text" in fn
    assert "\\/begin\\/" in fn and "innerHTML" not in fn
