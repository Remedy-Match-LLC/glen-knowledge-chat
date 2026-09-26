"""The Miron violet-glass story is withheld from products not bottled in Miron glass.

Production, 2026-09-26, from Glen's bottle photos: the MSM lotions are resold in 8 fl oz
plastic squeeze bottles and list an emulsifier. Their pages showed the comparison table
("Miron biophotonic violet glass", microplastic exposure "none", "Excipient-free") and
the "How Miron violet glass is made" video. A product with miron_glass: false shows none
of it; every other product is unchanged.
"""
import importlib

import pytest

from dashboard.product_page_sections import filter_sections

LOTIONS = ("pure--natural-msm-lotion", "lavender-msm-lotion", "coconut-rose-msm-lotion")
SECTIONS = [{"id": x} for x in ("intro", "video", "ingredients", "comparison", "cta")]


def test_filter_withholds_the_comparison_when_not_in_miron():
    ids = [s["id"] for s in filter_sections(SECTIONS, has_ingredients=True, has_own_video=False,
                                            in_miron=False)]
    assert "comparison" not in ids and "ingredients" in ids


def test_filter_keeps_the_comparison_by_default():
    ids = [s["id"] for s in filter_sections(SECTIONS, has_ingredients=True, has_own_video=False)]
    assert "comparison" in ids


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


def _page(a, slug):
    return a.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()


@pytest.mark.parametrize("slug", LOTIONS)
def test_resold_lotions_carry_no_miron_story(appmod, slug):
    d = _page(appmod, slug)
    ids = [s["id"] for s in d["sections"]]
    assert "comparison" not in ids
    vids = [v for s in d["sections"] if s["id"] == "video" for v in s["body"]["videos"]]
    assert not any((v.get("kind") == "educational") for v in vids), vids
    assert d["miron_assets"] == [] and d["miron_story"] == []


def test_a_house_bottled_product_keeps_its_miron_story(appmod):
    d = _page(appmod, "apoptogenesis")
    assert "comparison" in [s["id"] for s in d["sections"]]
    assert d["miron_assets"]


def test_no_empty_watch_section_when_the_miron_video_is_withheld():
    ids = [s["id"] for s in filter_sections(SECTIONS, has_ingredients=True, has_own_video=False,
                                            in_miron=False)]
    assert "video" not in ids
    ids = [s["id"] for s in filter_sections(SECTIONS, has_ingredients=True, has_own_video=True,
                                            in_miron=False)]
    assert "video" in ids
