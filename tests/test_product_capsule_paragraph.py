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
CHLOROPHYLL = ("Each capsule is a chlorophyll vegicap, made mainly from plant cellulose. It "
               "contains no gelatin or other animal products.")


def test_the_wording_is_glens_word_for_word():
    assert C.CAPSULE_COPY["drcaps"]["text"] == DRCAPS
    assert C.CAPSULE_COPY["pullulan"]["text"] == PULLULAN
    assert C.CAPSULE_COPY["pullulan"]["em"] == ["A. pullulans"]
    assert C.CAPSULE_COPY["chlorophyll"]["text"] == CHLOROPHYLL
    assert C.CAPSULE_COPY["chlorophyll"]["em"] == []


def _products():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]


def test_which_products_carry_which_paragraph():
    P = _products()
    kinds = {s: C.capsule_kind(s, p) for s, p in P.items()}
    assert {s for s, k in kinds.items() if k == "drcaps"} == {
        "glutathione-syntropy", "lipid-zyme", "scar-silk", "alkalize-bicarbonate-blend",
        "microbiome", "lens-zyme", "vitamin-c-syntropy", "clear-the-way", "fibrosolve"}
    assert kinds["spike-shield"] == "pullulan" and kinds["spleen-support"] == "pullulan"
    # Glen, 2026-10-03: "Lens-Zyme is enteric, as is vitamin C. DHT is pullulan"
    assert kinds["dht-blocker"] == "pullulan"
    # Every other capsule bottle is pullulan, except the three not yet confirmed.
    caps = {s for s, p in P.items() if str(p.get("bottle_type") or "").lower()
            in ("30 caps", "120 caps")}
    pinned_pullulan = {s for s, k in C.CAPSULE_BY_SLUG.items() if k == "pullulan"}
    assert {s for s in caps if kinds[s] == "pullulan"} == (
        (caps - set(C.CAPSULE_BY_SLUG) - set(C.UNCONFIRMED)) | (pinned_pullulan & caps))
    # Glen, 2026-10-03: chlorophyll vegicaps until their next production run.
    for s in ("appestat", "migrafree", "iron-syntropy"):
        assert kinds[s] == "chlorophyll"
    # Not a capsule: a dropper gets nothing.
    assert C.capsule_for("x", {"bottle_type": "Dropper 50 mL"}) is None


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
    assert _ing_body(appmod, "dht-blocker")["capsule"]["text"] == PULLULAN
    assert _ing_body(appmod, "lens-zyme")["capsule"]["text"] == DRCAPS
    assert _ing_body(appmod, "appestat")["capsule"]["text"] == CHLOROPHYLL
    assert _ing_body(appmod, "vitreous-vitality")["capsule"]["text"] == PULLULAN


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


def test_dht_blocker_copy_says_pullulan_only():
    """Glen, 2026-10-03: "DHT is pullulan". Its description also said "enteric"."""
    d = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]["dht-blocker"]
    assert "enteric" not in d["description"].lower()
    assert "30 pullulan vegicaps per bottle" in d["description"]


def test_the_chlorophyll_three_never_fall_through_to_pullulan():
    """Their bottles are "30 Caps", so without the hand mapping they would read as
    pullulan, which is wrong for the jars on the shelf until the next production run."""
    P = _products()
    for s in ("appestat", "migrafree", "iron-syntropy"):
        assert str(P[s].get("bottle_type") or "").lower() in C.CAPSULE_BOTTLES, s
        assert C.capsule_kind(s, P[s]) != "pullulan", s
