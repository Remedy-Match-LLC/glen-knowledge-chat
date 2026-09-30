"""Aller-Free drops carry Glen's approved wording, pinned (Glen "yes" via knowledge, 2026-09-30).

Source: knowledge/07 Knowledge-Base/formulation-copy/aller-free-websites-2026-09-30.json, the
old store copy with two claims softened, the Immune Modulation line and the FDA disclaimer.
Pinned so no AI draft replaces it; research is pinned too, so no unreviewed claims are added.
"""
import importlib
import json
import sqlite3

import pytest

SLUG = "allerfree-homeoenergetic-drops"
INTRO = ("Aller-Free HomeoEnergetic Drops supports comfort with airborne allergy symptoms without "
         "drowsiness or suppression of immune functions. Aller-Free was formulated by Rev. Dr. Glen "
         "Swartwout and has been relied on for its clinical effectiveness for over 20 years. As a "
         "homeo-energetic formula, Aller-Free is gentle, with no known side effects.")
IM = "For allergy and immune support, Dr. Glen's current recommendation is Immune Modulation."
FDA = ("The statements herein have not been evaluated by the Food and Drug Administration. This is "
       "not intended to diagnose, treat, cure, or prevent any disease.")


@pytest.fixture(scope="module")
def entry():
    return json.load(open("data/products.json"))["products"][SLUG]


def test_the_intro_is_glens_wording(entry):
    assert entry["intro"] == INTRO


def test_the_description_carries_the_immune_modulation_line_and_the_disclaimer(entry):
    d = entry["description"]
    assert d.startswith(INTRO)
    assert IM in d and FDA in d
    assert "50 mL dropper." in d and "Yucca filamentosa." in d
    assert "Price:" not in d


def test_directions_and_the_rest_are_unchanged(entry):
    assert entry["directions"] == "10 drops 3 times a day or as needed."
    assert entry["price_cents"] == 6997 and entry["bottle_type"] == "Dropper 50 mL"
    assert entry["pinecone_title"] == "Aller-Free HomeoEnergetic Drops"


def test_intro_description_and_research_are_pinned(entry):
    assert {"intro", "description", "research"} <= set(entry["copy_pinned"])


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
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def test_no_ai_draft_replaces_the_pinned_copy(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT MARKER fast relief")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    assert "AI DRAFT MARKER" not in json.dumps(d)
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    assert "ai" not in secs["intro"] and "ai" not in secs["description"]
