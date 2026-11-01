"""Macular Wellness Safranal: the vinpocetine line and the label directions (2026-10-31).

Spec: production/05 Formulations/_store-updates/2026-10-31-macular-wellness-safranal-contents.md,
"App". The October 2025 label's Bioavailability Factors list vinpocetine, and store 68 carries
the line. The panel rows are still open with Glen, so only the directions are pinned, and the
corrections entry keeps the rows' source: a missing source replays as "manual", which would
mark the rows confirmed.
"""
import copy
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "macular-wellness-safranal"
WARNING = ("This formula contains vinpocetine, so do not use it if you are pregnant or could "
           "become pregnant.")
DIRECTIONS = "1 to 3 capsules daily with meals away from other carotenoids, or as guided."


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


def _check(p):
    assert p["warning"] == WARNING and p["chat_caution"] is True
    assert p["directions"] == DIRECTIONS
    assert p["copy_pinned"] == ["directions"]   # no "ingredients" until the panel spec
    assert p["ingredients_source"] == "db-formulations-2026-06-25"
    assert p["content_since"].endswith("+00:00")


@pytest.mark.parametrize("name", ["products.json", "products-manual-corrections.json"])
def test_both_files_carry_it(name):
    _check(_load(name)[SLUG])


def test_the_replay_restores_it_onto_a_record_without_it():
    from scripts.apply_enrichment import apply_corrections
    old = copy.deepcopy(_load("products.json")[SLUG])
    for k in ("warning", "chat_caution", "directions", "copy_pinned", "content_since"):
        old.pop(k)
    products = {SLUG: old}
    apply_corrections(products, {SLUG: _load("products-manual-corrections.json")[SLUG]})
    _check(products[SLUG])


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as a
    importlib.reload(a)
    return a


@pytest.mark.parametrize("q", ["Can I take Macular Wellness Safranal while pregnant?",
                               "How many Macular Wellness Safranal should I take?"])
def test_the_chat_facts_carry_the_caution_and_the_directions(appmod, q):
    block = appmod._named_product_facts(q) or ""
    assert WARNING in block, q
    assert f"Directions: {DIRECTIONS}" in block, q
