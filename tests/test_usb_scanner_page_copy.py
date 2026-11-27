"""Marketing's USB scanner page text, Glen 2026-11-26 platform tab:
"yes, publish marketing's USB page text"."""
import pathlib

import pytest
import re

import app

SLUG = "bioenergetic-wellness-scanner-usb"


def _rec():
    return app._PRODUCTS["products"][SLUG]


def test_name_and_text():
    r = _rec()
    assert r["display_name"] == "Energy4Life (E4L) Bioenergetic Wellness Scanner, USB"

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


@pytest.mark.parametrize("name", [
    "Bioenergetic Wellness Scanner - USB", "Bioenergetic Wellness Scanner (USB)",
    "Energy4Life (E4L) Bioenergetic Wellness Scanner, USB"])
def test_old_and_new_names_resolve_to_the_page(name):
    from dashboard.practitioner_portal import exact_name_to_slug, name_to_slug
    products = app._PRODUCTS["products"]
    assert name_to_slug(name, products) == SLUG
    assert exact_name_to_slug(name, products) == SLUG


def _render(body, keep):
    """Run the page's own renderDescriptionBody in node, with a tiny DOM."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" /
           "begin-product.html").read_text()
    fn = src[src.index("function renderDescriptionBody"):]
    fn = fn[:fn.index("function renderVideoBody")]
    js = r"""
    function node(t){return {tag:t,kids:[],attrs:{},className:'',textContent:'',
      setAttribute:function(k,v){this.attrs[k]=v},appendChild:function(c){this.kids.push(c)}}}
    var document={createElement:function(t){return node(t)},
      createTextNode:function(x){return {tag:'#text',text:x}}};
    """ + fn + r"""
    var p=renderDescriptionBody(process.argv[1], process.argv[2]==='1');
    var out=p.kids.length?p.kids.map(function(k){return k.tag==='a'?{a:k.href,t:k.textContent}:{t:k.text}}):[{t:p.textContent}];
    console.log(JSON.stringify(out));
    """
    import json
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "-e", js, body, "1" if keep else "0"],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_an_approved_description_links_same_site_paths_only():
    out = _render("See the [Bluetooth version](/begin/product/x-y) page. "
                  "[evil](javascript:alert(1)) [ext](https://evil.example/begin/x) "
                  "[pr](//evil.example/begin/x)", True)
    links = [o["a"] for o in out if "a" in o]
    assert links == ["/begin/product/x-y"]
    assert [o["t"] for o in out if "a" in o] == ["Bluetooth version"]
    assert "javascript:alert(1)" in "".join(o["t"] for o in out if "a" not in o)


def test_an_unapproved_description_is_plain_text():
    out = _render("See the [Bluetooth version](/begin/product/x-y) page.", False)
    assert out == [{"t": "See the [Bluetooth version](/begin/product/x-y) page."}]


def test_the_buy_page_shows_the_link_words_not_the_syntax():
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" /
           "begin-buy.html").read_text()
    line = src[src.index("getElementById('p-desc').textContent"):]
    line = line[:line.index(";")]
    assert ".replace(" in line and "'$1'" in line


def test_the_chat_facts_show_the_link_words_not_the_syntax():
    out = app._named_product_facts(
        "Tell me about the Energy4Life (E4L) Bioenergetic Wellness Scanner, USB")
    text = out if isinstance(out, str) else repr(out)
    assert "Bluetooth version" in text
    assert "](/begin/" not in text
    assert "Energy4Life (E4L) Bioenergetic Wellness Scanner, USB" in text


def test_the_quickbooks_name_is_unchanged():
    # QBO finds the sales item by `name`; the new title lives in display_name so the
    # Black Friday sales do not open a second QBO item (review round 1).
    import json
    import subprocess
    rec = app._PRODUCTS["products"][SLUG]
    main = json.loads(subprocess.check_output(
        ["git", "show", "origin/main:data/products.json"],
        cwd=pathlib.Path(__file__).resolve().parents[1]))["products"][SLUG]
    assert rec["name"] == main["name"] == "Bioenergetic Wellness Scanner - USB"
    assert rec["display_name"] == "Energy4Life (E4L) Bioenergetic Wellness Scanner, USB"
