"""Seven catalogue changes Glen approved with one "yes" in the platform tab, 2026-10-26.

The doses follow the formulation tab's store-page specs of 2026-10-25: Vitamin C Syntropy
(about 12,200 IU of vitamin A a capsule from the reprint), Vitamin A Syntropy (25,000 IU),
Apoptogenesis, Appestat and Breast Support. Live, the bot gave Vitamin C Syntropy as 1 to 4
a day at 1,667 IU (knowledge, log 2386) and invented a rising Breast Support dose (logs 2375
and 2376). Also Breast Support's puerarin (raw 5207, Pueraria lobata at 98%), Focus
Neuro-Magnesium Powder's D3 per scoop, and Free & Easy's crocin, which is from gardenia.
"""
import json
import os
from datetime import datetime, timezone

import pytest

import app

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
DOSES = {
    "vitamin-c-syntropy": "1 capsule daily before food, as guided.",
    "vitamin-a-syntropy": "1 capsule daily with food, as guided.",
    "apoptogenesis": "1 to 2 capsules a day with food, or as guided.",
    "appestat": "1 capsule 30 to 45 minutes before meals, up to 2 a day, or as guided.",
    "breast-support": "1 capsule up to 6 times a day with food, or as guided.",
}
OLD = ("1 to 4 capsules", "1 to 3 capsules", "1 to 3 times", "one month", "Phase 1 terrain",
       "1667 IU", "Pueraria mirifica", "up to 10", "before every meal", "starts low")
QUESTIONS = {
    "vitamin-c-syntropy": ("How many Vitamin C Syntropy capsules a day?", "Vitamin C Syntropy"),
    "vitamin-a-syntropy": ("how much vitamin a syntropy should I take?", "Vitamin A Syntropy"),
    "apoptogenesis": ("apoptogenesis dose?", "Apoptogenesis"),
    "appestat": ("How do I take Appestat?", "Appestat"),
    "breast-support": ("How many Breast Support capsules a day?", "Breast Support"),
}


def _products():
    return json.load(open(os.path.join(DATA, "products.json")))["products"]


def _corr():
    return json.load(open(os.path.join(DATA, "products-manual-corrections.json")))


@pytest.mark.parametrize("slug", sorted(DOSES))
def test_the_record_carries_the_new_dose(slug):
    p = _products()[slug]
    assert p["directions"] == DOSES[slug]
    assert "directions" in p["copy_pinned"]
    for k in ("directions", "description", "intro", "learn_markdown"):
        for old in OLD:
            assert old not in (p.get(k) or ""), (k, old)
    c = _corr()[slug]
    assert c["directions"] == DOSES[slug]


@pytest.mark.parametrize("slug", sorted(DOSES))
def test_the_chat_gives_only_the_new_dose(slug):
    q, name = QUESTIONS[slug]
    b = app._named_product_facts(q)
    assert f"### {name}\n" in b
    assert f"Directions: {DOSES[slug]}" in b
    assert f"The Directions above are the only dose for {name}." in b
    # The rule names the withdrawn dose on purpose; nothing else may.
    rest = "\n".join(l for l in b.split("\n") if not l.startswith("The Directions above"))
    for old in OLD:
        assert old not in rest, old


def test_vitamin_c_syntropy_vitamin_a():
    p = _products()["vitamin-c-syntropy"]
    row = {"name": "Vitamin A (retinyl palmitate)", "dose": "3,660 mcg RAE (12,200 IU)"}
    assert row in p["ingredients"] and row in _corr()["vitamin-c-syntropy"]["ingredients"]
    assert "Vitamin A (retinyl palmitate) 3,660 mcg RAE (12,200 IU);" in p["learn_markdown"]
    b = app._named_product_facts("How much vitamin A is in Vitamin C Syntropy?")
    assert "- Vitamin A (retinyl palmitate): 3,660 mcg RAE (12,200 IU)" in b
    assert "Give no percent Daily Value that is not listed above" in b


def test_vitamin_a_syntropy_keeps_its_caution():
    b = app._named_product_facts("How much Vitamin A Syntropy should I take?")
    assert "Caution: Avoid during pregnancy or when planning a pregnancy." in b


def test_breast_support_puerarin():
    p = _products()["breast-support"]
    row = {"name": "Puerarin 98% (Pueraria lobata)", "dose": "10 mg"}
    assert row in p["ingredients"] and row in _corr()["breast-support"]["ingredients"]
    assert "mirifica" not in json.dumps(p)


@pytest.mark.parametrize("q", [
    # Only the catalog casing "Breast Support" names it without a cue.
    "Breast support while nursing?",
    "what gives breast support while nursing?",
    "Which bra gives the best breast support?",
])
def test_breast_support_as_a_phrase_brings_no_block(q):
    assert "### Breast Support\n" not in app._named_product_facts(q) + "\n"


def test_breast_support_by_its_catalog_name_brings_the_block():
    assert "### Breast Support\n" in app._named_product_facts("Is Breast Support safe while nursing?")


@pytest.mark.parametrize("q", [
    "how much breast support does a sports bra give?",
    "breast support dose?",
])
def test_breast_support_in_lowercase_brings_no_block(q):
    """Review round 1: lowercase over-matched an everyday phrase, so only the catalog
    casing names it."""
    assert "### Breast Support\n" not in app._named_product_facts(q) + "\n"


@pytest.mark.parametrize("q, name", [
    ("how much apoptogenesis a day?", "Apoptogenesis"),
    ("how many appestat per day", "Appestat"),
    ("how many appestat daily?", "Appestat"),
])
def test_a_day_after_a_lowercase_name_is_a_cue(q, name):
    """Review round 1: these dose questions got no block."""
    assert f"### {name}\n" in app._named_product_facts(q)


@pytest.mark.parametrize("q", [
    "how much apoptogenesis per day in healthy tissue",
    "how much brain cleanse a day happens during sleep",
    "what is neuro magnesium daily intake for adults",
    "I take stress release daily walks",
])
def test_a_day_inside_a_longer_phrase_is_no_cue(q):
    """Review round 3: these biology and everyday sentences brought a product block."""
    assert "###" not in app._named_product_facts(q)


def test_focus_d3_per_scoop():
    for rec in (_products()["focus-neuro-magnesium-powder"], _corr()["focus-neuro-magnesium-powder"]):
        d3 = [i for i in rec["ingredients"] if i["name"] == "Vitamin D3 (Cholecalciferol)"]
        assert d3 == [{"name": "Vitamin D3 (Cholecalciferol)", "dose": "325 IU"}]


def test_free_and_easy_crocin_is_from_gardenia():
    p = _products()["free-and-easy"]
    names = [i["name"] for i in p["ingredients"]]
    assert "Crocin 45% (Gardenia jasminoides)" in names and "Crocin" not in names
    assert _corr()["free-and-easy"]["ingredients"] == p["ingredients"]


@pytest.mark.parametrize("slug", ["free-and-easy", "breast-support"])
def test_content_since_is_past_and_replayed(slug):
    """It drops the drafts that said saffron or carried no dose. A future time would drop
    every draft written before it, so it must be in the past."""
    since = _products()[slug]["content_since"]
    assert datetime.fromisoformat(since) < datetime.now(timezone.utc)
    assert _corr()[slug]["content_since"] == since


@pytest.mark.parametrize("slug", sorted(DOSES) + ["focus-neuro-magnesium-powder", "free-and-easy"])
def test_the_corrections_replay_restores_the_approved_record(slug):
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    approved = _products()[slug]
    # What a re-import writes back: the old dose in every text field.
    rec = dict(approved, directions="1 to 4 capsules daily or as guided.", copy_pinned=[],
               ingredients=[{"name": "Crocin", "dose": "48 mg"}])
    texts = [k for k in ("description", "intro", "learn_markdown") if approved.get(k)
             and slug in DOSES and slug != "breast-support"]
    for k in texts:
        rec[k] = "Take 1 to 4 capsules daily or as guided."
    rec.pop("content_since", None)
    p = {slug: rec}
    ae.apply_corrections(p, {slug: _corr()[slug]})
    ae.apply_corrections(p, {slug: _corr()[slug]})
    keys = ["ingredients"] + (["directions", "copy_pinned"] if slug in DOSES else []) + texts
    keys += ["content_since"] if "content_since" in approved else []
    for k in keys:
        assert p[slug][k] == approved[k], k
