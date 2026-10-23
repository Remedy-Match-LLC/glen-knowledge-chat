"""Clarity's "What this does" is Glen's approved wording, pinned (Glen "approved", 2026-10-23).

Source: production/05 Formulations/clarity/2026-10-23/clarity-store-page.html, "Proposed page
text", the four body paragraphs. It replaces an unreviewed eye-only AI draft that said "One to
three capsules daily". The formula is one capsule daily, for brain and eyes.
"""
import importlib
import json
import sqlite3

import pytest

SLUG = "clarity"


@pytest.fixture(scope="module")
def entry():
    return json.load(open("data/products.json"))["products"][SLUG]


def test_the_intro_is_the_four_approved_paragraphs(entry):
    paras = entry["intro"].split("\n\n")
    assert len(paras) == 4
    assert paras[0].startswith("Clarity supports both your eyes and your brain.")
    assert paras[1].startswith("Rosmarinic acid is the heart of Clarity.")
    assert paras[2].startswith("Pterostilbene is a natural polyphenol from blueberries")
    assert paras[3] == "Piperine, from black pepper, helps your body absorb polyphenols like these."
    assert "One to three" not in entry["intro"]


def test_only_the_intro_is_pinned(entry):
    assert entry["copy_pinned"] == ["intro"]


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


def test_the_page_shows_the_pinned_intro_over_an_ai_draft(appmod, entry):
    from dashboard import sales_pages as sp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sp.upsert_section(cx, SLUG, "intro", "AI DRAFT MARKER One to three capsules daily")
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == entry["intro"]
    assert "ai" not in secs["intro"]
    assert "AI DRAFT MARKER" not in json.dumps(secs["intro"])
