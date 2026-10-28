# tests/test_complete_formula_line.py
"""A pinned ingredient panel is the whole formula, and the bot is told so.

Glen, 2026-10-28, "yes to the chat fix". Formulation's read-back of Crystalline Clarity
after d92fc4e2: asked "Does Crystalline Clarity contain selenium?", the bot said "Yes ...
86 mcg of selenium" in 3 of 3 tries. The panel has no selenium; retrieved old-formula
text did. The block now says the panel overrides other sources and an unlisted
ingredient gets a "no".
"""
import app

LINE = ("These are the current label ingredients and amounts for {n}. They override any "
        "other source, including the reference text above. When another source says {n} "
        "contains an ingredient that is not on this list, and is not a form, source or part of "
        "one on it, that source describes an older formula: do not say {n} contains it.")
# Pinned by hand, so a change to _UMBRELLA_ROW or the catalog shows up here (round 1).
WITH_LINE = sorted([
    "adrenal-syntropy", "angiogenx", "angiostasis", "b17-max", "b17-syntropy", "clarity",
    "clear-the-way", "crystalline-clarity", "estro-clear", "fibrolysis-factors",
    "iron-syntropy", "lcarnosine", "melissa-20-1-extract-powder", "moisture-eyes-night-oil",
    "moisturize", "n-acetyl-l-carnosine", "neuro-magnesium", "pterostilbene-pure-powder",
    "stress-release", "vitamin-a-syntropy", "vitamin-d-syntropy", "vitreous-vitality",
    "wash--rinse", "zinc-taste-test", "zinc-taste-test-drops"])
MARK = "These are the current label ingredients and amounts for"
BLEND = ("These are the current label amounts for {n}. They override any other source about "
         "these ingredients, including the reference text above. A blend or group on this list "
         "holds ingredients it does not name, so never say {n} lacks an ingredient because it "
         "is not listed.")


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
            after = block.split("Ingredients from the label:\n", 1)[1].split("\n")
            n_rows = next(i for i, ln in enumerate(after + [""]) if not ln.startswith("- "))
            panel = after[:n_rows]
            umbrella = any(app._UMBRELLA_ROW.search(ln) for ln in panel)
            assert (MARK in block) == (not umbrella), slug
            if not umbrella:
                assert block.count(LINE.format(n=name)) == 1, slug
                # Directly after the panel, before anything else in the block.
                assert after[n_rows].startswith(MARK), slug
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
        assert block.count(BLEND.format(n=name)) == 1, slug
        checked += 1
    # Every listed slug that has a pinned panel today is checked, none skipped quietly.
    pinned = [s for s in UMBRELLA if "ingredients" in app._pinned_copy(products.get(s, {}))]
    assert checked == len(pinned) and checked >= 8, (checked, pinned)


def test_the_capsule_is_left_to_the_description():
    block = app.named_product_facts_block("Does Crystalline Clarity contain pullulan?")
    assert "does not contain" not in block   # absence is never inferred from the list
    assert "Pullulan" in block   # the approved description names the capsule


def test_two_products_each_get_their_own_line():
    block = app.named_product_facts_block(
        "Does Estro-Clear or Crystalline Clarity contain selenium?")
    for n in ("Estro-Clear", "Crystalline Clarity"):
        if f"### {n}" in block and "Ingredients from the label:" in block.split(f"### {n}", 1)[1]:
            assert block.count(LINE.format(n=n)) == 1, n
    assert block.count(MARK) == 2


def test_exactly_these_products_get_the_line():
    got = sorted(s for s, p in _products().items() if (p.get("name") or "").strip()
                 and MARK in (app._named_product_facts(f"What is in {p['name'].strip()}?") or ""))
    assert got == WITH_LINE
