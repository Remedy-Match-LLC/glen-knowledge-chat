"""The Serrapeptase ingredient pages list every formula that contains serrapeptase,
built from the formula data, with Clear the Way first (Glen, 2026-10-07). Before this the
exact-name match linked Fibrosolve alone."""
import json
import os

from dashboard import ingredients as I

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPSULE_PAGES = ["Serrapeptase",
                 "Serrapeptase 400,000 u/g (Serratiopeptidase) (Serratia marcescens)",
                 "Serrapeptase bacterium in gut of Bombyx mori"]


def test_each_capsule_page_lists_the_four_formulas_clear_the_way_first():
    for name in CAPSULE_PAGES:
        got = I.formulations_with(name)
        assert [f["slug"] for f in got] == ["clear-the-way", "scar-silk", "fibrosolve", "lipid-zyme"], name
        assert got[0]["name"] == "Clear the Way"
        assert "enteric capsule" in got[0]["note"]
        assert all("note" not in f for f in got[1:])


def test_every_capsule_page_slug_is_a_real_ingredient_page():
    for name in CAPSULE_PAGES:
        assert I.resolve(I.slugify(name))["name"] == name


def test_the_off_sale_powder_is_never_listed():
    for name in CAPSULE_PAGES:
        assert "serrapeptase" not in [f["slug"] for f in I.formulations_with(name)]


def test_the_homeopathic_page_keeps_the_exact_match():
    got = [f["slug"] for f in I.formulations_with("Serrapeptase (Serratia marcescens)")]
    assert got == ["ocuheal-eye-drops", "ocuheal-plus-eye-drops"]


def test_an_ungrouped_ingredient_is_unchanged():
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]
    want = [s for s, p in prods.items()
            if any(I.slugify((i.get("name") if isinstance(i, dict) else i) or "") == "fulvic-acid"
                   for i in (p.get("ingredients") or []))]
    assert [f["slug"] for f in I.formulations_with("Fulvic Acid")] == want


def test_an_inactive_grouped_product_is_dropped(monkeypatch, tmp_path):
    prods = json.load(open(os.path.join(ROOT, "data", "products.json")))
    prods["products"]["scar-silk"]["inactive"] = True
    f = tmp_path / "products.json"
    f.write_text(json.dumps(prods))
    monkeypatch.setattr(I, "_PRODUCTS", f)
    assert "scar-silk" not in [x["slug"] for x in I.formulations_with("Serrapeptase")]


def test_the_page_shows_the_note_as_text_not_html():
    html = open(os.path.join(ROOT, "static", "begin-ingredient.html")).read()
    i = html.index("function renderFormulations")
    block = html[i:html.index("return wrap;", html.index("formulations.forEach", i))]
    assert "note.textContent = ' ' + f.note" in block and "innerHTML" not in block


def test_the_atlas_serrapeptase_concept_links_clear_the_way():
    for f in ("atlas-concepts.json", "atlas-seed-input.json"):
        raw = open(os.path.join(ROOT, "data", f), encoding="utf-8").read()
        assert "/begin/product/serrapeptase\"" not in raw, f
    concepts = json.load(open(os.path.join(ROOT, "data", "atlas-concepts.json")))["concepts"]
    c = next(c for c in concepts if c["id"] == "serrapeptase")
    prod = [l for l in c["links"] if l.get("type") == "product"]
    assert prod == [{"source": "remedymatch", "title": "Clear the Way", "type": "product",
                     "url": "https://myhealingoasis.com/begin/product/clear-the-way"}]
    seed = json.load(open(os.path.join(ROOT, "data", "atlas-seed-input.json")))["concepts"]
    links = [l for c in seed if (c.get("label") or "") == "Serrapeptase" for l in c.get("links") or []]
    assert [(l["title"], l["url"]) for l in links if l.get("type") == "product"] == [
        ("Clear the Way", "https://myhealingoasis.com/begin/product/clear-the-way")]


def test_a_failed_read_is_retried_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(I, "_GROUPS_CACHE", None)
    good = I._GROUPS
    monkeypatch.setattr(I, "_GROUPS", tmp_path / "missing.json")
    assert I._groups_by_page() == {}
    monkeypatch.setattr(I, "_GROUPS", good)
    assert "serrapeptase" in I._groups_by_page()


def test_the_generator_skips_an_off_sale_formula(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bif", os.path.join(ROOT, "scripts", "build_ingredient_formulations.py"))
    bif = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bif)
    if not os.path.exists(bif.DB):
        import pytest
        pytest.skip("formula database is on Glen's machine only")
    prods = json.load(open(bif.PRODUCTS))
    prods["products"]["scar-silk"]["inactive"] = True
    pj = tmp_path / "products.json"
    pj.write_text(json.dumps(prods))
    monkeypatch.setattr(bif, "PRODUCTS", str(pj))
    monkeypatch.setattr(bif, "OUT", str(tmp_path / "out.json"))
    bif.build()
    got = json.load(open(tmp_path / "out.json"))["groups"]["serrapeptase"]["products"]
    assert [i["slug"] for i in got] == ["clear-the-way", "fibrosolve", "lipid-zyme"]
