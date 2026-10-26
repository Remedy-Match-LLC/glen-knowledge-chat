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
CLOSED = ("Availability: Neuro Magnesium cannot be ordered now. Never say it is available "
          "to order or in stock.")


@pytest.fixture(autouse=True)
def _fresh_founding_cache(monkeypatch):
    monkeypatch.setattr(app, "_FOUNDING_AVAILABILITY_CACHE", {})


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


def test_the_chat_path_carries_the_block():
    """named_product_facts_block is what chat() appends to the user message."""
    b = app.named_product_facts_block("What is in Neuro Magnesium?")
    assert "### Neuro Magnesium" in b and "- Vitamin D3 (Cholecalciferol): 650 IU" in b


@pytest.mark.parametrize("q", [
    "Do neuro magnesium forms like threonate help memory?",
    "Tell me about Focus Neuro-Magnesium Powder",
    # The stocked jar typed with a space, or a lowercase name beside "Focus" (review
    # round 1, 2026-10-25).
    "How much Focus Neuro Magnesium Powder should I take?",
    "I bought the Focus jar. What is the neuro magnesium dose?",
    "I take Focus Neuro-Magnesium Powder. How much neuro magnesium should I take?",
    "How much Focus-Neuro Magnesium Powder should I take?",
    # The hyphenated name is both products (production, 2026-09-28): no block guesses.
    "What is in Neuro-Magnesium?",
])
def test_no_block_for_a_phrase_or_the_other_product(q):
    assert "### Neuro Magnesium\n" not in app._named_product_facts(q) + "\n"


@pytest.mark.parametrize("q", [
    "Does Neuro Magnesium help focus?",
    # An everyday lowercase "focus" is no jar (review round 3, 2026-10-25).
    "how much neuro magnesium should I take for focus?",
    "I lose focus. how do I take neuro magnesium?",
])
def test_an_everyday_focus_keeps_the_block(q):
    assert "### Neuro Magnesium\n" in app._named_product_facts(q)


def _founding(monkeypatch, enabled, is_open):
    from dashboard import founding as fd
    monkeypatch.setattr(app, "_founding_enabled", lambda: enabled)
    monkeypatch.setattr(fd, "is_open", lambda cx, slug, now_iso=None: is_open)


def test_an_open_presale_is_never_called_in_stock(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    _founding(monkeypatch, True, True)
    b = app._named_product_facts("Is Neuro Magnesium available to order?")
    assert ("Availability: Neuro Magnesium is not made yet. It can be reserved on its page "
            "as Founding Batch No. 1, and the card is charged when it ships. Never say it is "
            "in stock or ready to ship.") in b


@pytest.mark.parametrize("enabled,is_open", [(True, False), (False, True)])
def test_a_closed_presale_cannot_be_ordered(monkeypatch, tmp_path, enabled, is_open):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    _founding(monkeypatch, enabled, is_open)
    b = app._named_product_facts("Is Neuro Magnesium available to order?")
    assert CLOSED in b
    assert "reserved" not in b and "waiting list" not in b


def test_a_failed_read_says_it_cannot_be_ordered(monkeypatch, tmp_path):
    from dashboard import founding as fd
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "_founding_enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(fd, "is_open", boom)
    b = app._named_product_facts("Is Neuro Magnesium available to order?")
    assert CLOSED in b


def test_the_presale_state_is_read_once_a_minute(monkeypatch, tmp_path):
    from dashboard import founding as fd
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(app, "_founding_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(fd, "is_open", lambda *a, **k: calls.append(1) or True)
    for _ in range(3):
        app.named_product_facts_block("Is Neuro Magnesium safe to take?")
    assert len(calls) == 1
    clock = [app.time.monotonic() + 61]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    app._named_product_facts("Is Neuro Magnesium available to order?")
    assert len(calls) == 2


def test_no_founding_line_for_another_product():
    b = app._named_product_facts("How much B17 Syntropy should I take?")
    assert "Availability:" not in b


def test_the_corrections_replay_keeps_the_approved_record():
    import importlib
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    ae = importlib.import_module("apply_enrichment")
    corr = json.load(open(os.path.join(DATA, "products-manual-corrections.json")))
    approved = _record()
    rec = dict(approved)
    # What a re-import writes back first: the old 750 IU panel and none of the approval.
    rec.update(ingredients=[dict(i) for i in PANEL[:-1]]
               + [{"name": "Vitamin D3 (Cholecalciferol)", "dose": "750 IU"}],
               ingredients_source="vault-locked-panel-2026-05-31")
    for k in ("warning", "directions", "copy_pinned", "content_since", "chat_caution"):
        rec.pop(k, None)
    p = {SLUG: rec}
    ae.apply_corrections(p, {SLUG: corr[SLUG]})
    for k in ("ingredients", "directions", "warning", "chat_caution", "copy_pinned",
              "content_since", "ingredients_source"):
        assert p[SLUG][k] == approved[k], k
    assert p[SLUG]["ingredients"] == PANEL
