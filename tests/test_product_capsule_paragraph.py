"""The capsule paragraph on product pages (Glen, 2026-10-03, via marketing).

The shell is hidden from the ingredient list and described in its own
paragraph. Both texts are Glen's, word for word."""
import importlib
import json
import os
import subprocess

import pytest

from dashboard import capsule_copy as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRCAPS = ("Each capsule is a phthalate-free DRcaps™ delayed-release vegicap, made of "
          "hypromellose and gellan gum. It is designed to protect its contents from stomach "
          "acid and delay their release. Most enteric capsules and coatings rely on "
          "phthalates. This one does not, and it carries no added chemicals or solvents.")
PULLULAN = ("A. pullulans is a beneficial endophyte inside many food plants. A. pullulans is a "
            "naturally occurring mycorrhizal fungus that functions as an epiphyte in the "
            "health-promoting microbiome of many food plants from grapes to green beans, and "
            "also participates symbiotically as a beneficial endophyte inside the plants. It is "
            "used agriculturally in biological control of plant diseases.")


def test_the_wording_is_glens_word_for_word():
    assert C.CAPSULE_COPY["drcaps"]["text"] == DRCAPS
    assert C.CAPSULE_COPY["pullulan"]["text"] == PULLULAN
    assert C.CAPSULE_COPY["pullulan"]["em"] == ["A. pullulans"]


def test_which_products_carry_which_paragraph():
    assert {s for s, k in C.CAPSULE_BY_SLUG.items() if k == "drcaps"} == {
        "glutathione-syntropy", "lipid-zyme", "scar-silk", "alkalize-bicarbonate-blend"}
    assert C.capsule_for("spike-shield")["text"] == PULLULAN
    assert C.capsule_for("microbiome") is None


def test_every_mapped_slug_is_a_real_product():
    products = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    assert not [s for s in C.CAPSULE_BY_SLUG if s not in products]


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
    return a


def _ing_body(appmod, slug):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    return next(s for s in d["sections"] if s["id"] == "ingredients")["body"]


def test_the_page_data_carries_the_paragraph(appmod):
    assert _ing_body(appmod, "lipid-zyme")["capsule"]["text"] == DRCAPS
    assert _ing_body(appmod, "microbiome")["capsule"] is None


def test_the_page_sets_a_pullulans_in_italics_and_nothing_else():
    """Run the page's own renderer under node against a minimal DOM stub."""
    html = open(os.path.join(ROOT, "static", "begin-product.html"), encoding="utf-8").read()
    start = html.index("      var cap = body && body.capsule;")
    end = html.index("      // Suggested use, served straight from the catalog")
    block = html[start:end]
    js = """
    function el(tag){ return {tag: tag, kids: [], className: '', textContent: '',
      appendChild: function(c){ this.kids.push(c); }}; }
    var document = { createElement: el,
      createTextNode: function(t){ return {tag: '#text', textContent: t}; } };
    var wrapEl = el('div');
    var body = {capsule: %s};
    %s
    var p = wrapEl.kids[0];
    console.log(JSON.stringify(p.kids.map(function(k){ return [k.tag, k.textContent]; })));
    """ % (json.dumps(C.capsule_for("spike-shield")), block)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    parts = json.loads(out.stdout)
    assert [t for tag, t in parts if tag == "em"] == ["A. pullulans", "A. pullulans"]
    assert "".join(t for _, t in parts) == PULLULAN
