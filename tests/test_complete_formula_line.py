# tests/test_complete_formula_line.py
"""A pinned ingredient panel is the whole formula, and the bot is told so.

Glen, 2026-10-28, "yes to the chat fix". Formulation's read-back of Crystalline Clarity
after d92fc4e2: asked "Does Crystalline Clarity contain selenium?", the bot said "Yes ...
86 mcg of selenium" in 3 of 3 tries. The panel has no selenium; retrieved old-formula
text did. The block now says the panel overrides other sources and an unlisted
ingredient gets a "no".
"""
import app

LINE = ("This is the complete current formula of {n}. It overrides any other source, "
        "including the reference text above. An ingredient not on this list is not in {n}, "
        "so answer no.")


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
            assert "This is the complete current formula of" in block, slug
            seen_with += 1
        else:
            assert "This is the complete current formula of" not in block, slug
            seen_without += 1
    assert seen_with >= 10, seen_with   # the check reached real pinned panels
    assert seen_without >= 1, seen_without
