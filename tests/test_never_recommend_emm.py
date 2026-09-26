"""Electrolyte Mineral Manna is never recommended (Glen's standing rule). It may be listed
and sold, never volunteered. Production, 2026-09-26: its own page carried a "Dr. Glen
recommends" box, and hand picks skipped the never-recommend list."""
import importlib

import pytest

from dashboard import related_products as rp

PRODUCTS = {"a": {"name": "A"}, "electrolyte-mineral-manna": {"name": "EMM"},
            "fungifuge": {"name": "Fungifuge"}, "b": {"name": "B"}}


def test_a_hand_pick_of_a_never_recommend_product_is_dropped():
    res = rp.resolve_related("a", manual=["electrolyte-mineral-manna", "fungifuge", "b"],
                             harvested=[], semantic=[], products=PRODUCTS)
    shown = res["featured"] + res["more"]
    assert "electrolyte-mineral-manna" not in shown and "fungifuge" not in shown
    assert "b" in shown


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("RELATED_PRODUCTS_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_RELATED_PRODUCTS_ENABLED", True)
    monkeypatch.setattr(a, "_related_semantic", lambda slug: ["magnesium-taurate"])
    return a


def test_the_emm_page_has_no_recommends_box(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/electrolyte-mineral-manna").get_json()
    assert "related" not in [s["id"] for s in d["sections"]]
    assert "recommends" not in str([s.get("title") for s in d["sections"]]).lower()


def test_another_page_still_has_its_box(appmod):
    d = appmod.app.test_client().get("/begin/product-page-data/apoptogenesis").get_json()
    assert "related" in [s["id"] for s in d["sections"]]
