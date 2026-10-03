"""Store ingredient panels: no 0 mg rows, no lost decimals, no placeholder names.

2026-10-03, marketing and production: Vitreous Vitality showed "Chromium
Polynicotinate 4835 mg"; FileMaker's bill of materials says 0.4835 mg. Sixteen
sub-milligram doses had lost their "0." the same way (MSC 0.085 mg shown as
85 mg). Glen: "Don't list the 0 mg ingredients", and "the 0 mg lines are notes to
consider for future formulation updates": hidden on pages, kept in the data.
18 live pages showed "(unnamed FMP ingredient N)" though FileMaker names every one.
"""
import importlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZERO = re.compile(r"^\s*0(\.0+)?\s*(mg|mcg|g|µg|iu)?\s*$", re.I)


def _panels(fname):
    doc = json.load(open(os.path.join(ROOT, "data", fname), encoding="utf-8"))
    products = doc.get("products", doc)
    for slug, p in products.items():
        if isinstance(p, dict) and isinstance(p.get("ingredients"), list):
            for ing in p["ingredients"]:
                if isinstance(ing, dict):
                    yield slug, ing


def test_zero_dose_rows_stay_in_the_data_as_glens_notes():
    zero = [(sl, i["name"]) for sl, i in _panels("products.json")
            if ZERO.match(str(i.get("dose") or ""))]
    assert ("vitreous-vitality", "Goji") in zero


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


def _doses(rows):
    return {r["name"]: r.get("dose") for r in rows if isinstance(r, dict)}


def test_the_product_page_hides_zero_dose_rows(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/vitreous-vitality").get_json()
    ings = next(s for s in d["sections"] if s["id"] == "ingredients")["body"]["ingredients"]
    shown = _doses(ings)
    assert "Goji" not in shown and "Black Goji" in shown
    assert not [n for n, v in shown.items() if ZERO.match(str(v or ""))]


def test_the_product_data_route_hides_zero_dose_rows(appmod):
    d = appmod.app.test_client().get("/begin/product-data/vitreous-vitality").get_json()
    shown = _doses(d["ingredients"])
    assert "Goji" not in shown and "Black Goji" in shown


def test_ai_copy_grounding_leaves_out_zero_dose_rows():
    from dashboard import product_content as pc
    out = pc._page_text_from_product({"description": "d", "ingredients": [
        {"name": "Goji", "dose": "0 mg"}, {"name": "MSM", "dose": "20 mg"}]})
    assert "Goji" not in out["text"] and "MSM 20 mg" in out["text"]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_no_placeholder_names(fname):
    bad = [s for s, i in _panels(fname) if "unnamed FMP ingredient" in str(i.get("name"))]
    assert not bad, bad[:5]


def test_vitreous_vitality_chromium_is_micrograms():
    doses = {i["name"]: i["dose"] for s, i in _panels("products.json")
             if s == "vitreous-vitality"}
    assert doses["Chromium Polynicotinate"] == "483.5 mcg"
    assert doses["MSC"] == "85 mcg"


def _gen():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_panels", os.path.join(ROOT, "scripts", "generate_panels_from_db.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_generator_writes_sub_mg_as_mcg_and_keeps_zero_rows():
    g = _gen()
    panel, why = g.build_panel([
        {"ingredient_name": "Chromium Polynicotinate", "dose": 0.4835, "dose_unit": "mg"},
        {"ingredient_name": "Goji", "dose": 0, "dose_unit": "mg"},
        {"ingredient_name": "MSM", "dose": 20, "dose_unit": "mg"},
    ])
    assert why is None
    assert panel == [{"name": "Chromium Polynicotinate", "dose": "483.5 mcg"},
                     {"name": "Goji", "dose": "0 mg"},
                     {"name": "MSM", "dose": "20 mg"}]


def test_the_capsule_shell_is_hidden_but_kept_in_the_data(appmod):
    """Glen, 2026-10-03: "hide the capsule shell" (FileMaker 5122)."""
    raw = {i["name"] for sl, i in _panels("products.json") if sl == "lipid-zyme"}
    assert "Enteric Acid Resistant Vegi Capsule 00" in raw
    d = appmod.app.test_client().get("/begin/product-page-data/lipid-zyme").get_json()
    ings = next(s for s in d["sections"] if s["id"] == "ingredients")["body"]["ingredients"]
    assert "Enteric Acid Resistant Vegi Capsule 00" not in _doses(ings)
    assert _doses(ings)


def test_microbiomes_spelling_of_the_shell_is_hidden_too():
    from dashboard.products import shown_ingredients
    assert shown_ingredients([{"name": "Enteric acid-resistant vegicap 00", "dose": "1 ea."},
                              {"name": "Inulin", "dose": "50 mg"}]) == [
        {"name": "Inulin", "dose": "50 mg"}]
