"""Soothe Powder is dosed by the scoop, and the Soothe capsule page retires into it.

Glen, 2026-10-02: "1 scoop 2 times a day in drinks." Glen, 2026-10-18: "1 500 mg scoop",
and "yes" to the store line below. Specs, production pillar:
  05 Formulations/_store-updates/2026-10-18-soothe-powder-scoop.md
  05 Formulations/soothe-powder/2026-10-02/retire-soothe-page-spec.md

The store said "1 capsule 2 times a day in drinks". Soothe capsules are not made
("same formula, but Soothe would be capsules (inactive)"), so `soothe` retires with
`superseded_by: "soothe-powder"`.
"""
import json

import pytest

import app
from dashboard import biofield_portal_publish as bpp
from dashboard import products as products_mod

LINE = "1 scoop (500 mg) 2 times a day in drinks."
FORBIDDEN = ("capsule", "1 to 2 scoops", "3 times")
SIX_PAGES = ("binder-complex", "cocos-nucifera", "fiber-cleanse", "gut-terrain-program",
             "terrain-restore", "vitreous-vitality")


@pytest.fixture(scope="module")
def catalog():
    """The repo file itself, never via DATA_DIR, which other tests repoint."""
    return json.load(open("data/products.json"))["products"]


def _strings(value, path="soothe-powder"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")


# ── the dose line ──
def test_directions_and_description_are_the_approved_line(catalog):
    p = catalog["soothe-powder"]
    assert p["directions"] == LINE
    assert p["description"] == LINE


def test_no_value_in_soothe_powder_gives_the_old_dose(catalog):
    """Every nested string value, any case. The page's structural `capsule` key comes from
    `capsule_for` at render time, not from this entry."""
    found = [(path, s) for path, s in _strings(catalog["soothe-powder"])
             for pat in FORBIDDEN if pat in s.lower()]
    assert found == []


def test_only_the_directions_are_pinned_and_content_since_is_unset(catalog):
    p = catalog["soothe-powder"]
    assert p["copy_pinned"] == ["directions"]
    assert "content_since" not in p


# ── the retirement ──
def test_soothe_is_retired_into_the_powder(catalog):
    p = catalog["soothe"]
    assert p.get("inactive") is True
    assert p.get("superseded_by") == "soothe-powder"


def test_soothe_redirects_to_a_live_powder(catalog):
    assert products_mod.superseded_slug("soothe", catalog) == "soothe-powder"
    assert not catalog["soothe-powder"].get("inactive")
    assert products_mod.superseded_slug("soothe-powder", catalog) == "soothe-powder"


def test_a_remedy_named_soothe_resolves_to_the_powder(catalog):
    assert bpp.resolve_remedy_slug("Soothe", catalog) == "soothe-powder"


def test_filemaker_454_maps_to_the_powder():
    assert json.load(open("data/fmp_slug_map.json"))["resolved"]["454"] == "soothe-powder"


def test_the_six_related_lists_carry_the_powder():
    related = json.load(open("data/related-harvested.json"))
    for page in SIX_PAGES:
        assert "soothe-powder" in related[page], page
    assert [k for k, v in related.items() if isinstance(v, list) and "soothe" in v] == []
    assert "soothe-powder" not in (related.get("soothe-powder") or [])


def test_no_clinical_theory_link_goes_to_the_capsule_page():
    text = open("data/clinical_theory_catalog.json").read()
    assert "/begin/product/soothe\"" not in text
    assert "/begin/product/soothe-powder\"" in text


# ── the chat facts block ──
@pytest.mark.parametrize("question", ["How do I take Soothe Powder?",
                                      "how do i take soothe powder?"])
def test_the_chat_block_carries_the_directions_and_no_description(question):
    block = app.named_product_facts_block(question)
    assert "### Soothe Powder" in block, question
    assert f"Directions: {LINE}" in block
    # The description is the same line. Unpinned, it must not appear on its own.
    assert block.count(LINE) == 1
    assert "capsule" not in block[block.index("### Soothe Powder"):].lower()


def test_the_retired_name_gets_no_block():
    assert "### Soothe" not in app.named_product_facts_block("How do I take Soothe?")
