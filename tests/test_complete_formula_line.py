# tests/test_complete_formula_line.py
"""A pinned ingredient panel is the whole formula, and the bot is told so.

Glen, 2026-10-28, "yes to the chat fix". Formulation's read-back of Crystalline Clarity
after d92fc4e2: asked "Does Crystalline Clarity contain selenium?", the bot said "Yes ...
86 mcg of selenium" in 3 of 3 tries. The panel has no selenium; retrieved old-formula
text did. The block now says the panel overrides other sources and an unlisted
ingredient gets a "no".
"""
import app

LINE = ("These are all the active ingredients in the current formula of {n}. They override "
        "any other source about what {n} contains, including the reference text above. "
        "Asked about an ingredient that is not on this list, and is not a form or part of one "
        "on it, say the current formula of {n} does not contain it. The capsule and other "
        "inactive ingredients are not on this list: answer about those only from the "
        "description.")
MARK = "These are all the active ingredients in the current formula of"


def _products():
    return (app._PRODUCTS or {}).get("products", {}) or {}


def test_the_selenium_question_gets_the_complete_formula_line():
    block = app.named_product_facts_block("Does Crystalline Clarity contain selenium?")
    line = LINE.format(n="Crystalline Clarity")
    assert line in block
    # Right after the panel, so it reads as part of it.
    panel = block.index("Ingredients from the label:\n")
    assert panel < block.index(line)
    assert "selenium" not in block[panel:block.index(line)].lower()


def test_every_pinned_panel_carries_it_and_no_other_block_does():
    seen_with = seen_without = 0
    for slug, p in _products().items():
        name = (p.get("name") or "").strip()
        if not name:
            continue
        block = app._named_product_facts(f"What is in {name}?")
        if not block or f"### {name}" not in block:
            continue
        if "Ingredients from the label:" in block:
            panel = block.split("Ingredients from the label:\n", 1)[1].split("\n\n", 1)[0]
            umbrella = any(app._UMBRELLA_ROW.search(ln) for ln in panel.splitlines())
            assert (MARK in block) == (not umbrella), slug
            seen_with += not umbrella
        else:
            assert MARK not in block, slug
            seen_without += 1
    assert seen_with >= 10, seen_with   # the check reached real pinned panels
    assert seen_without >= 1, seen_without


UMBRELLA = ["curcu-guard", "lens-zyme", "vitamin-c-syntropy", "apoptogenesis", "dht-blocker",
            "acetaldehyde-detox", "appestat", "natural-resource-az", "reverse-age",
            "focus-neuro-magnesium-powder", "aces-eye-drops"]


def test_a_blend_or_catch_all_panel_gets_no_line():
    """Bioavailability Blend holds ginkgo and licorice; "Amino Acids (all)" holds glycine.
    A "not on this list" rule would turn a true yes into a no (review round 1)."""
    products = _products()
    checked = 0
    for slug in UMBRELLA:
        name = (products.get(slug, {}).get("name") or "").strip()
        block = app._named_product_facts(f"Does {name} contain ginkgo?") if name else ""
        if "Ingredients from the label:" not in (block or ""):
            continue
        assert MARK not in block, slug
        checked += 1
    assert checked >= 8, checked


def test_the_capsule_is_left_to_the_description():
    block = app.named_product_facts_block("Does Crystalline Clarity contain pullulan?")
    assert "The capsule and other inactive ingredients are not on this list" in block
    assert "Pullulan" in block   # the approved description names the capsule
