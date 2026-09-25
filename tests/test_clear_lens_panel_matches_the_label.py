"""Clear Lens Eyedrops: the panel matches Glen's label, 2026-09-25.

The live doses were shifted one row: the base blank, MSM 97%, silver 0.001%. Spec:
production/05 Formulations/clear-lens-eye-drops/2026-09-25/panel-correction-spec.md
(Glen "approved"). Saffron is Crocus sativus (Glen "yes"). Checked through
/begin/product-page-data, by exact ordered (name, dose) equality; an empty dose fails.
"""
import copy
import importlib

import pytest

SLUG = "clear-lens-eye-drops"
PANEL = [("Quintessential Bioterrain Restore", "97%"), ("MSM (Methylsulfonylmethane)", "1%"),
         ("DMSO (Dimethylsulfoxide)", "1%"), ("N-Acetyl L-Carnosine", "1%"),
         ("Micellized Vitamin A (as Acetate)", "0.1%"), ("Vitamin C (as Zinc-Ascorbate)", "0.1%"),
         ("Lanosterol", "0.02%"), ("Saffron (Crocus sativus)", "0.02%"),
         ("Vitamin E Complex", "0.02%"), ("Cineraria maritima (succus)", "0.01%"),
         ("Silver (Ionic)", "0.0001%")]


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


def _pairs(a):
    d = a.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    sec = next(s for s in d["sections"] if s["id"] == "ingredients")
    return [(i["name"], i["dose"]) for i in sec["body"]["ingredients"]]


def test_the_page_serves_exactly_the_label_panel(appmod):
    got = _pairs(appmod)
    assert all(dose for _n, dose in got), "an empty dose is a failure"
    assert got == PANEL


def test_the_check_fails_with_one_amount_wrong(appmod, monkeypatch):
    prods = copy.deepcopy(appmod._PRODUCTS)
    prods["products"][SLUG]["ingredients"][-1]["dose"] = "0.001%"
    monkeypatch.setattr(appmod, "_PRODUCTS", prods)
    assert _pairs(appmod) != PANEL


def test_no_stale_figure_or_spelling_is_served(appmod):
    shown = str(_pairs(appmod))
    assert "0.001%" not in shown and "sativa" not in shown and "Quinton seawater" not in shown


def test_the_panel_is_pinned_and_sourced_from_the_label(appmod):
    p = appmod._PRODUCTS["products"][SLUG]
    assert "ingredients" in p["copy_pinned"]
    assert p["ingredients_source"] == "label-2026-09-25"
    assert all("*" not in i["name"] for i in p["ingredients"]), "no markdown in the catalog"
