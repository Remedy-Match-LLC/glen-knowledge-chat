"""OcuHeal and OcuHeal+ show the same panel except DMSO and the base.

Glen told a client "the one change from the OcuHeal you had in April is 10% DMSO", but the
pages disagreed on five other rows: OcuHeal's came from a stale cached AI card built on old
store copy. Spec: production/05 Formulations/ocuheal-plus/2026-09-25/panel-correction-spec.md,
Glen "approved" 2026-09-25. Carnosine is 1% in both (Glen, 2026-09-25). Order follows the
panel image, not the percentages. Checked through /begin/product-page-data, the endpoint
the page reads, by EXACT equality of (name, dose) pairs: a pairwise walk would stop at a
shorter list, and a percentage is never parsed out of a name.
"""
import importlib

import pytest

TAIL = [("Forskolin (Coleus forskohlii)", "0.1%"), ("Puerarin (Pueraria lobata)", "0.1%"),
        ("Vitamin A (Retinol)", "0.1%"), ("Vitamin B2 (Riboflavin 5-Phosphate)", "0.1%"),
        ("Vitamin B6 (Pyridoxal 5-Phosphate)", "0.1%"), ("Vitamin C & Zinc (Zinc Ascorbate)", "0.1%"),
        ("Vitamin E Complex", "0.01%"), ("Lanosterol", "0.01%"),
        ("Safranal 3% (Crocus sativus)", "0.1%"),
        ("Cineraria (Cineraria maritima) (succus)", "0.1%"), ("Silver (Nano)", "0.0001%"),
        ("Serrapeptase (Serratia marcescens)", "5C")]
EXPECTED = {
    "ocuheal-eye-drops": [("Quintessential Bioterrain Restore", "96%"),
                          ("MSM (Methylsulfonylmethane)", "1%"), ("N-Acetyl L-Carnosine", "1%"),
                          ("DMSO (Dimethylsulfoxide)", "0.5%")] + TAIL,
    "ocuheal-plus-eye-drops": [("Quintessential Bioterrain Restore", "86.5%"),
                               ("DMSO (Dimethylsulfoxide)", "10%"),
                               ("MSM (Methylsulfonylmethane)", "1%"),
                               ("N-Acetyl L-Carnosine", "1%")] + TAIL,
}
OCU_TEXT = ("OcuHeal Eye Drops carry botanical and nutritional ingredients for the whole eye, "
            "including the retina, lens, cornea, conjunctiva, lacrimal glands and tear film. The "
            "base is Quinton sea water (Quintessential Bioterrain Restore). Use 1 drop in each "
            "eye 2 times a day.")


STALE = "STALE AI DRAFT TEXT"


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    """AI drafts ON and a stale draft seeded for every narrative section, so a passing
    pin test proves the pin (round 1: without SALES_PAGES_AI_COPY no draft is ever
    considered, and the pin tests passed with copy_pinned removed)."""
    import sqlite3
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    from dashboard import sales_pages as sp
    with sqlite3.connect(a.LOG_DB) as cx:
        for slug in EXPECTED:
            for sec in ("intro", "description", "research"):
                sp.upsert_section(cx, slug, sec, STALE)
    return a


def _get(a, slug):
    return a.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()


def _section(data, sid):
    return next(s for s in data["sections"] if s["id"] == sid)


def _pairs(data):
    return [(i["name"], i["dose"]) for i in _section(data, "ingredients")["body"]["ingredients"]]


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_the_page_serves_exactly_the_bottle_panel(appmod, slug):
    got = _pairs(_get(appmod, slug))
    assert all(dose for _n, dose in got), "an empty dose is a failure"
    assert got == EXPECTED[slug]


def test_only_dmso_and_the_base_differ_on_the_served_pages(appmod):
    a = dict(_pairs(_get(appmod, "ocuheal-eye-drops")))
    b = dict(_pairs(_get(appmod, "ocuheal-plus-eye-drops")))
    assert {n for n in a if a[n] != b.get(n)} == {"Quintessential Bioterrain Restore",
                                                   "DMSO (Dimethylsulfoxide)"}


def test_the_equality_check_catches_one_wrong_percentage(appmod, monkeypatch):
    """Spec: prove the check once with one percentage wrong, through the endpoint."""
    import copy
    prods = copy.deepcopy(appmod._PRODUCTS)
    for i in prods["products"]["ocuheal-eye-drops"]["ingredients"]:
        if i["name"] == "N-Acetyl L-Carnosine":
            i["dose"] = "0.5%"
    monkeypatch.setattr(appmod, "_PRODUCTS", prods)
    assert _pairs(_get(appmod, "ocuheal-eye-drops")) != EXPECTED["ocuheal-eye-drops"]


def test_ocuheal_serves_its_approved_text_over_a_stale_draft(appmod):
    data = _get(appmod, "ocuheal-eye-drops")
    for sid in ("intro", "description"):
        sec = _section(data, sid)
        assert "ai" not in sec, f"{sid} must be pinned"
        assert sec["body"] == OCU_TEXT, sid   # equality: extra words must fail (round 3)
    assert _section(data, "ingredients")["body"]["directions"] == "1 drop in each eye 2 times a day."
    shown = str([_section(data, s)["body"] for s in ("intro", "description", "ingredients")])
    for stale in ("levetates", "Quintessential Terrain Restore) 96%", "(10 ppm)", "mirifica"):
        assert stale not in shown, stale


def test_ocuheal_plus_serves_its_approved_intro_over_a_stale_draft(appmod):
    from tests.test_ocuheal_plus_listing import APPROVED_INTRO
    data = _get(appmod, "ocuheal-plus-eye-drops")
    for sid in ("intro", "description"):
        sec = _section(data, sid)
        assert "ai" not in sec, sid
        assert sec["body"] == APPROVED_INTRO, sid   # equality, not containment (round 3)


def test_research_is_left_alone_until_knowledge_replaces_the_copy(appmod):
    """Spec: research and learn are a separate step; this change does not pin them."""
    assert _section(_get(appmod, "ocuheal-eye-drops"), "research").get("ai") == "cached"


def test_the_buy_page_shows_no_stale_generated_benefits_for_ocuheal(appmod):
    """Round 1: /begin/buy reads /begin/product-data; OcuHeal's benefits came from the
    stale cached card. Pinning benefits with no catalog list withholds them."""
    from dashboard import product_content as pc
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(pc, "LOG_DB", appmod.LOG_DB)
    try:
        pc._cache_put("ocuheal-eye-drops", "card",
                      {"description": "", "ingredients": ["Cineraria maritima (10 ppm)"],
                       "benefits": ["STALE BENEFIT FROM THE OLD CARD"]}, [])
        assert pc._cache_get("ocuheal-eye-drops", "card"), "setup: the stale card must be cached"
        data = appmod.app.test_client().get("/begin/product-data/ocuheal-eye-drops").get_json()
        assert "STALE BENEFIT" not in str(data.get("benefits"))
    finally:
        mp.undo()
