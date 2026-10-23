"""Clarity's "What this does" is Glen's approved wording, pinned (Glen "approved", 2026-10-23).

Source: marketing/04 Copy/clarity-store-page-2026-10-23/clarity-store-page.md, the ten body
paragraphs, one sentence each, after three review rounds. It replaces an unreviewed eye-only
AI draft that said "One to three capsules daily". The formula is one capsule daily, for brain and eyes.
"""
import importlib
import json
import sqlite3

import pytest

SLUG = "clarity"


@pytest.fixture(scope="module")
def entry():
    return json.load(open("data/products.json"))["products"][SLUG]


PARAS = [
    "Clarity supports both your eyes and your brain.",
    "Your retina develops from the same tissue as your brain.",
    "So your retina and your brain share many of the same needs.",
    "Rosmarinic acid, concentrated from rosemary, is the heart of Clarity.",
    "In a laboratory study, it helped keep human lens proteins clear outside the body.",
    "That study inspired this formula.",
    "Pterostilbene is a natural polyphenol found in blueberries, and a close relative of resveratrol.",
    "In studies of aging animals, it supported memory and healthy brain function.",
    "In cell and animal studies, it also protected delicate eye tissues.",
    "A small amount of piperine, from black pepper, is included.",
]


def test_the_intro_is_the_ten_approved_paragraphs(entry):
    assert entry["intro"].split("\n\n") == PARAS
    assert "One to three" not in entry["intro"]
    assert "absorb" not in entry["intro"]


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
