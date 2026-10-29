# tests/test_complete_formula_line.py
"""A pinned ingredient panel is the whole formula, and the bot is told so.

Glen, 2026-10-28, "yes to the chat fix". Formulation's read-back of Crystalline Clarity
after d92fc4e2: asked "Does Crystalline Clarity contain selenium?", the bot said "Yes ...
86 mcg of selenium" in 3 of 3 tries. The panel has no selenium; retrieved old-formula
text did. The block now says the panel overrides other sources and an unlisted
ingredient gets a "no".
"""
import pytest

import app

LINE = ("These are the current label ingredients and amounts for {n}. They override every "
        "retrieved snippet. When a snippet says {n} contains an active ingredient that is not "
        "on this list or in the description above, and is not a form, source or part of one "
        "on it, that snippet describes an older formula: do not say {n} contains it. This rule "
        "is about active ingredients only, never the capsule or other inactive ingredients.")
MARK = "These are the current label ingredients and amounts for"
BLEND = ("These are the current label amounts for {n}. They override every retrieved snippet "
         "about these ingredients. A blend or group on this list holds ingredients it does not "
         "name, so never say {n} lacks an ingredient because it is not listed.")
# Pinned by hand, so a change to _UMBRELLA_ROW or the catalog shows up here (round 1).
WITH_LINE = sorted([
    "adrenal-syntropy", "angiogenx", "angiostasis", "b17-max", "b17-syntropy", "clarity",
    "clear-the-way", "crystalline-clarity", "estro-clear", "fibrolysis-factors", "lcarnosine",
    "melissa-20-1-extract-powder", "moisture-eyes-night-oil", "moisturize",
    "n-acetyl-l-carnosine", "neuro-magnesium", "pterostilbene-pure-powder",
    "vitamin-d-syntropy", "wash--rinse", "zinc-taste-test", "zinc-taste-test-drops"])


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
            umbrella = any(app._UMBRELLA_ROW.search(ln.split(". Role: ")[0]) for ln in panel)
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


def test_a_role_naming_a_group_does_not_make_a_catch_all(monkeypatch):
    """Only the row name counts, never its Role text (review round 2)."""
    slug = "estro-clear"
    p = _products()[slug]
    name = p["name"].strip()
    roles = dict(p.get("ingredient_roles") or {})
    first = next(i["name"] for i in p["ingredients"] if isinstance(i, dict) and i.get("name"))
    roles[first] = "supports collagen in a proprietary complex blend"
    monkeypatch.setitem(p, "ingredient_roles", roles)
    block = app._named_product_facts(f"What is in {name}?")
    assert "Role: supports collagen" in block
    assert block.count(LINE.format(n=name)) == 1



@pytest.mark.parametrize("row", [
    "- Bioavailability Blend: 34.5 mg", "- Amino Acids (all)", "- Polyphenols (Proprietary Formula)",
    "- Vitamin E Complex", "- Polyphenols: 260 mg", "- Pacific Ocean ORMUS",
    "- Quinton Ocean ORMUS & Trace Mineral Source", "- Natural Scents",
    "- CBD Full Spectrum (Cannabis spp.): 1 mg", "- Fatty Acid Esters (plant derived emulsifier)",
    "- Hydrolyzed Whey (Bos taurus)", "- Hydrolyzed Collagen (grass-fed bovine)",
    "- Quinton (Micro-filtered Seawater)", "- Quintessential Bioterrain Restore",
    "- Humic Acid", "- Fulvic Acid", "- Polysaccharides (Aloe vera)", "- Phosphatides 50%",
    "- CBD (cannabidiol, Cannabis spp.)", "- Bromelain: 350 mg proprietary blend with protease",
])
def test_each_catch_all_form_is_caught(row):
    """One row per alternative, so a narrowed regex fails here before a new product
    ships with that row (review round 3)."""
    assert app._UMBRELLA_ROW.search(row), row


@pytest.mark.parametrize("row", [
    "- Zinc (as zinc L-carnosine, 109 mg): 25 mg", "- L-ergothioneine 98%: 25 mg",
    "- Green tea extract, polyphenols 98%, EGCG 50% (Camellia sinensis)", "- Mogroside V 50%",
])
def test_a_named_ingredient_is_not_a_catch_all(row):
    assert not app._UMBRELLA_ROW.search(row), row
