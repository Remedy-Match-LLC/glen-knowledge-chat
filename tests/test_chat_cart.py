from dashboard.chat_cart import explicit_cart_items


CATALOG = [
    {"slug": "brain-boost", "name": "Brain Boost"},
    {"slug": "man-manna", "name": "Man Manna"},
    {"slug": "terrain-restore", "name": "Terrain Restore"},
]


def test_explicit_multi_product_command_with_quantities():
    assert explicit_cart_items(
        "Please add two Brain Boost and 3 bottles of Man Manna to my order", CATALOG
    ) == [
        {"slug": "brain-boost", "name": "Brain Boost", "qty": 2},
        {"slug": "man-manna", "name": "Man Manna", "qty": 3},
    ]


def test_question_or_recommendation_context_never_mutates_cart():
    assert explicit_cart_items("What does Brain Boost do?", CATALOG) == []
    assert explicit_cart_items("Do you recommend Terrain Restore?", CATALOG) == []
    assert explicit_cart_items("Do I need Brain Boost?", CATALOG) == []


def test_negated_order_command_never_mutates_cart():
    assert explicit_cart_items("Don't add Brain Boost", CATALOG) == []


def test_slug_and_quantity_cap_are_supported():
    assert explicit_cart_items("order 500 brain-boost", CATALOG) == [
        {"slug": "brain-boost", "name": "Brain Boost", "qty": 99}]


# ── "+" is a word, 2026-09-24 ─────────────────────────────────────────────────
# "OcuHeal+ Eye Drops" (10% DMSO) and "OcuHeal Eye Drops" (0.5%) once normalised to
# the same words, and a tie went to the longer slug: "add 2 OcuHeal Eye Drops" put
# the NEW product in the basket at the same price. The real catalog, built as the
# chat builds it, so every "+" product in it is covered.

def _real_catalog():
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    products = json.load(open(os.path.join(root, "data", "products.json")))["products"]
    return [{"slug": s, "name": p.get("name") or p.get("title") or s}
            for s, p in products.items() if not p.get("info_only") and not p.get("inactive")]


def _one(message, catalog):
    got = explicit_cart_items(message, catalog)
    assert len(got) == 1, got
    return got[0]["slug"], got[0]["qty"]


def test_ocuheal_and_ocuheal_plus_each_select_their_own_product():
    cat = _real_catalog()
    assert _one("add 2 OcuHeal Eye Drops", cat) == ("ocuheal-eye-drops", 2)
    assert _one("add 2 OcuHeal+ Eye Drops", cat) == ("ocuheal-plus-eye-drops", 2)
    assert _one("please order OcuHeal Plus Eye Drops", cat) == ("ocuheal-plus-eye-drops", 1)
    assert _one("add ocuheal-plus-eye-drops", cat) == ("ocuheal-plus-eye-drops", 1)


def test_both_in_one_message_are_two_lines():
    got = explicit_cart_items("add 1 OcuHeal+ Eye Drops and 2 OcuHeal Eye Drops", _real_catalog())
    assert sorted((i["slug"], i["qty"]) for i in got) == [
        ("ocuheal-eye-drops", 2), ("ocuheal-plus-eye-drops", 1)]


def test_every_plus_name_and_its_lookalike_selects_its_own_product():
    """Every "+" product in the real catalog, and every product whose name is a "+"
    name with the "+" dropped, picks itself. Covers future "+" products too."""
    import re
    cat = _real_catalog()
    loose = lambda n: " ".join(re.sub(r"[^a-z0-9]+", " ", n.lower()).split())
    plus_loose = {loose(p["name"]) for p in cat if "+" in p["name"]}
    checked = [p for p in cat if "+" in p["name"] or loose(p["name"]) in plus_loose]
    assert len(checked) >= 3, "the catalog lost its + products; this test checks nothing"
    wrong = [(p["name"], [i["slug"] for i in explicit_cart_items(f"add {p['name']}", cat)])
             for p in checked]
    wrong = [(n, got) for (n, got), p in zip(wrong, checked) if got[:1] != [p["slug"]]]
    assert not wrong, wrong


def test_a_plus_name_still_matches_as_typed_without_the_plus():
    """Before this change "Air & Surface PRO" matched "Air & Surface PRO+". It still does
    while no other product claims the loose form."""
    cat = [{"slug": "air-surface-pro-plus", "name": "Air & Surface PRO+"},
           {"slug": "neuro-eye-drops", "name": "Neuro+ Eye Drops"}]
    assert _one("add Air & Surface PRO", cat) == ("air-surface-pro-plus", 1)
    assert _one("add 3 Neuro Eye Drops", cat) == ("neuro-eye-drops", 3)


def test_the_loose_form_never_takes_another_products_name():
    cat = [{"slug": "ocuheal-eye-drops", "name": "OcuHeal Eye Drops"},
           {"slug": "ocuheal-plus-eye-drops", "name": "OcuHeal+ Eye Drops"}]
    assert _one("add OcuHeal Eye Drops", cat) == ("ocuheal-eye-drops", 1)
    # order of the catalog must not matter
    assert _one("add OcuHeal Eye Drops", cat[::-1]) == ("ocuheal-eye-drops", 1)


def test_the_shortlink_slug_keeps_plus_products_apart():
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-fake")
    os.environ.setdefault("PINECONE_API_KEY", "pcsk_fake")
    import app
    assert app._slugify_product("OcuHeal+ Eye Drops") == "ocuheal-plus-eye-drops"
    assert app._slugify_product("OcuHeal Eye Drops") == "ocuheal-eye-drops"
