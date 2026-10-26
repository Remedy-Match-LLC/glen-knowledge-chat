"""Neuro Magnesium's directions, caution and panel (Glen, 2026-10-25, platform tab).

FileMaker 1196, the Retina Renew presale. Glen said "yes" to the directions, the caution
and the store panel, then "650" for D3: FileMaker holds 0.65 mg a scoop of 500,000 IU/g
material, so 325 IU a scoop and 650 IU a day. The catalog said 750 IU.

With nothing pinned the product brought no facts block, and the live chat answered "what
is in Neuro Magnesium" with Focus Neuro-Magnesium Powder's panel (knowledge-69, logs 2359,
2361 and 2362).
"""
import json
import os

import pytest

import app

SLUG = "neuro-magnesium"
DIRECTIONS = "2 scoops a day in water, or as guided."
WARNING = ("Consult your health professional before use if you are pregnant or "
           "breastfeeding, take lithium or other prescription medications, or have kidney "
           "or thyroid disease.")
PANEL = [
    {"name": "Magnesium N-Acetyl-Taurate (ATA Mg)", "dose": "770 mg"},
    {"name": "L-Theanine", "dose": "100 mg"},
    {"name": "HydroCurc (cold-water-dispersible curcumin)", "dose": "49.8 mg"},
    {"name": "Apigenin", "dose": "20 mg"},
    {"name": "Mogroside V 50%", "dose": "30 mg"},
    {"name": "Lithium Orotate", "dose": "18 mg"},
    {"name": "Vitamin B6 (Pyridoxal-5-Phosphate)", "dose": "10 mg"},
    {"name": "Methylcobalamin (Vitamin B12)", "dose": "500 mcg"},
    {"name": "L-Methylfolate", "dose": "400 mcg"},
    {"name": "Vitamin D3 (Cholecalciferol)", "dose": "650 IU"},
]
# Focus Neuro-Magnesium Powder's panel lines, which the chat gave for this product.
FOCUS_ONLY = ("Threonate", "Zinc", "23.1 mg", "375 IU", "Lithium Orotate: 9 mg")
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _record():
    return json.load(open(os.path.join(DATA, "products.json")))["products"][SLUG]


def test_the_record_carries_the_approved_text():
    p = _record()
    assert p["directions"] == DIRECTIONS and p["warning"] == WARNING
    assert p["chat_caution"] is True
    assert p["copy_pinned"] == ["directions", "ingredients"]
    assert p["ingredients"] == PANEL
    # The description carries claims Glen has not approved for the bot.
    assert "description" not in p["copy_pinned"]


@pytest.mark.parametrize("q", [
    "What is in Neuro Magnesium?",
    "How do I take Neuro Magnesium?",
    "what is in neuro magnesium?",
    "how much neuro magnesium should I take?",
])
def test_the_chat_gets_its_own_panel_dose_and_caution(q):
    b = app._named_product_facts(q)
    assert "### Neuro Magnesium" in b
    assert f"Directions: {DIRECTIONS}" in b
    assert WARNING in b
    for line in PANEL:
        assert f"- {line['name']}: {line['dose']}" in b
    assert "750 IU" not in b
    for focus in FOCUS_ONLY:
        assert focus not in b
    # The unpinned description stays out.
    assert "blood-eye barrier" not in b


@pytest.mark.parametrize("q", [
    "Do neuro magnesium forms like threonate help memory?",
    "Tell me about Focus Neuro-Magnesium Powder",
])
def test_no_block_for_a_phrase_or_the_other_product(q):
    assert "### Neuro Magnesium\n" not in app._named_product_facts(q) + "\n"


def test_the_corrections_replay_keeps_the_approved_record():
    c = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))[SLUG]
    assert c["ingredients"] == PANEL
    assert c["directions"] == DIRECTIONS and c["warning"] == WARNING
    assert c["chat_caution"] is True
    assert c["copy_pinned"] == ["directions", "ingredients"]
    assert c["content_since"] == _record()["content_since"]
