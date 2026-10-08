"""Zinc Syntropy's intro is Glen's approved wording, pinned (Glen "yes" via production, 2026-10-07).

Spec: production/05 Formulations/_store-updates/2026-10-07-zinc-syntropy-intro.md. The cached AI
intro said "Zinc Synergy" and "antitrust". Only the intro is pinned; the other sections and the
Zinc Synergy alias stay as they were.
"""
import importlib
import json
import sqlite3

import pytest

SLUG = "zinc-syntropy"
INTRO = "\n\n".join([
    "Zinc is a cofactor for about 2,000 enzymes, more than any other mineral.",
    "Your body relies on it for immunity, wound healing, and the special senses: vision, hearing, "
    "taste and smell.",
    "Low zinc can show up as a craving for salty and spicy foods.",
    "Zinc Syntropy combines three forms of zinc: zinc L-carnosine, zinc bisglycinate and zinc citrate.",
    "Together they supply 21 mg of zinc in each capsule.",
    "L-carnosine adds antioxidant support, and mixed tocopherols bring the whole vitamin E family.",
    "A small amount of copper (1.5 mg) keeps zinc and copper in balance, because zinc taken alone "
    "over time can lower copper.",
    "Riboflavin 5-phosphate and vitamin D3 complete the formula.",
    "Phytates in grains bind zinc and reduce how much the body absorbs, so a grain-based diet can "
    "fall short even when intake looks adequate.",
])


@pytest.fixture(scope="module")
def entry():
    return json.load(open("data/products.json"))["products"][SLUG]


def test_the_intro_is_glens_wording_and_only_the_intro_is_pinned(entry):
    assert entry["intro"] == INTRO
    assert entry["copy_pinned"] == ["intro"]
    assert entry["aliases"] == ["Zinc Synergy"] and entry["fmp_id"] == "337"


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


def test_a_cached_ai_intro_cannot_replace_it(appmod):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.upsert_section(cx, SLUG, "intro", "Zinc Synergy AI DRAFT MARKER antitrust")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    assert "ai" not in secs["intro"]
    assert "AI DRAFT MARKER" not in json.dumps(d)
