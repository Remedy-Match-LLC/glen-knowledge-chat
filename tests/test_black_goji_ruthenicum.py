"""Black goji is Lycium ruthenicum on three app pages.

Spec: production/05 Formulations/_store-updates/2026-10-27-black-goji-ruthenicum.md
(sha256 ce5c841e4c03a551). All three use FileMaker raw 5130, bought as black goji. Only
the one row's name changes; its dose stays and it links to the black-goji page.
"""
import copy
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = "Black goji anthocyanins 25% (Lycium ruthenicum)"
ROWS = {"vitreous-vitality": "20 mg", "nous-energy": "48 mg", "blood-cleanse": "10 mg"}
OLD = {"vitreous-vitality": "Anthocyanins 25% (Lycium barbarum)",
       "nous-energy": "Black Goji Anthocyanins 25% (Lycium barbarum)",
       "blood-cleanse": "Black Goji"}
OLD_SLUGS = ["anthocyanins-25-lycium-barbarum", "black-goji-anthocyanins-25-lycium-barbar"]


def _data(name):
    return json.load(open(os.path.join(ROOT, "data", name)))


def _goji(entry):
    return [r for r in entry["ingredients"] if r["name"] == NEW]


@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
@pytest.mark.parametrize("slug", sorted(ROWS))
def test_the_row_is_renamed_with_its_dose_and_page(fname, slug):
    d = _data(fname)
    e = (d["products"] if "products" in d else d)[slug]
    assert _goji(e) == [{"name": NEW, "dose": ROWS[slug], "page_slug": "black-goji"}]
    assert "page_name" not in _goji(e)[0]
    assert not any(r["name"] == OLD[slug] for r in e["ingredients"])
    assert not any("barbarum" in r["name"] for r in e["ingredients"])


def test_the_corrections_rows_equal_the_catalogue():
    p, c = _data("products.json")["products"], _data("products-manual-corrections.json")
    for slug in ROWS:
        assert c[slug]["ingredients"] == p[slug]["ingredients"], slug


def test_the_replay_keeps_the_new_rows():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(_data("products.json")["products"])
    for slug in ROWS:
        for r in products[slug]["ingredients"]:
            if r["name"] == NEW:
                r.clear()
                r.update({"name": OLD[slug], "dose": ROWS[slug]})
    apply_corrections(products, _data("products-manual-corrections.json"))
    for slug in ROWS:
        assert _goji(products[slug]) == [{"name": NEW, "dose": ROWS[slug],
                                          "page_slug": "black-goji"}], slug


def test_the_black_goji_page_lists_all_three():
    from dashboard import ingredients
    ingredients._name_index.cache_clear()
    got = {r["slug"] for r in ingredients.formulations_with("Black Goji")}
    assert set(ROWS) <= got
    assert ingredients.resolve("black-goji") is not None


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    from dashboard import ingredients
    ingredients._name_index.cache_clear()
    return a


@pytest.mark.parametrize("slug", sorted(ROWS))
def test_the_page_shows_the_new_row(appmod, slug):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    body = {s["id"]: s for s in d["sections"]}["ingredients"]["body"]
    rows = [(i["name"], i["dose"], i["slug"]) for i in body["ingredients"]]
    assert (NEW, ROWS[slug], "black-goji") in rows


@pytest.mark.parametrize("old", OLD_SLUGS)
def test_the_old_barbarum_pages_are_gone(appmod, old):
    r = appmod.app.test_client().get(f"/begin/ingredient-page-data/{old}")
    assert r.status_code == 404 and r.get_json() == {"state": "unknown"}


def test_the_new_name_opens_no_second_page():
    """page_slug indexes the row under black-goji only; the new label text must not
    open a page of its own."""
    from dashboard import ingredients
    ingredients._name_index.cache_clear()
    assert ingredients.slugify(NEW) not in ingredients._name_index()
    assert ingredients.resolve(ingredients.slugify(NEW)) is None


def test_the_black_goji_ingredient_record_names_ruthenicum():
    """FileMaker raw 5130 was corrected on 2026-10-27 (production, Glen's yes). The page's
    copy prompt reads scientific and label_form from this file, which no job refreshes."""
    rec = _data("fmp-ingredient-content.json")["ingredients"]["black goji"]
    assert rec["scientific"] == "Lycium ruthenicum"
    assert rec["label_form"] == "Black Goji Anthocyanin 25% (Lycium ruthenicum)"
    assert rec["name"] == "Black Goji"


def test_the_black_goji_page_prompt_says_ruthenicum():
    """What the page's AI copy is written from."""
    from dashboard import ingredient_copy, ingredients
    ingredients._name_index.cache_clear()
    lines = ingredient_copy._fmp_lines(ingredients.resolve("black-goji"))
    assert "Lycium ruthenicum" in lines and "barbarum" not in lines
