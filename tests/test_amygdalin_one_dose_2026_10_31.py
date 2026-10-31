"""Amygdalin products take one dose a day (Glen, 2026-10-31, production tab).

B17 Max goes from up to 2 level scoops a day to 1, and Breast Support from up to 6 capsules
to 1. Wording and Biofield splits: production/05 Formulations/b17-max/2026-10-31/
one-dose-a-day.md. Breast Support keeps "or as guided" (Glen, same day: keep it on every
dosage instruction unless specified otherwise). B17 Max keeps its 25 October form, where
"as guided" governs only the increase.
"""
import json
import os
import sqlite3

import pytest

import app
from dashboard import biofield_authoring as ba

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
B17_TIMING = ("with cooked food. Increase slowly, as guided, up to 1 level scoop once a day. "
              "Do not exceed 1 level scoop a day. If you also take B17 Syntropy, take no "
              "more than 1 capsule a day, and count it as half a scoop toward the 1-scoop "
              "limit.")
DIRECTIONS = {
    "b17-max": "Start with a quarter of a level scoop once a day " + B17_TIMING,
    "breast-support": "Take 1 capsule once a day with food, or as guided.",
}
SPLITS = {
    "B17 Max": {"dosage": "1/4 level scoop", "frequency": "once a day", "timing": B17_TIMING},
    "Breast Support": {"dosage": "1 capsule", "frequency": "once a day",
                       "timing": "with food, or as guided."},
}
QUESTIONS = {
    "b17-max": ("How many scoops of B17 Max a day?", "B17 Max"),
    "breast-support": ("How many Breast Support capsules a day?", "Breast Support"),
}
WITHDRAWN = {"b17-max": "up to 2 level scoops", "breast-support": "up to 6"}


def _load(name):
    d = json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    return d.get("products", d)


@pytest.mark.parametrize("slug", sorted(DIRECTIONS))
@pytest.mark.parametrize("fname", ["products.json", "products-manual-corrections.json"])
def test_the_directions_give_one_dose_a_day(fname, slug):
    p = _load(fname)[slug]
    assert p["directions"] == DIRECTIONS[slug]
    assert "directions" in p["copy_pinned"]
    for v in p.values():
        if isinstance(v, str) and not v.startswith("Glen "):
            assert "2 level scoops" not in v and "6 times" not in v


def _snapshot():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, dosage TEXT, "
               "dosage_freq TEXT, dosage_timing TEXT)")
    cx.executemany("INSERT INTO fmp_snap_products VALUES (?,?,?,?)", [
        ("B17 Max", "1 capsule", "daily", "with food"),
        ("Breast Support", "1 capsule", "up to 6 times a day", "with food")])
    return cx


@pytest.mark.parametrize("name", sorted(SPLITS))
def test_the_biofield_split_comes_from_code_over_the_snapshot(name):
    assert ba.remedy_dosing(_snapshot(), name) == SPLITS[name]
    assert ba.remedy_dosing(_snapshot(), name.lower()) == SPLITS[name]


@pytest.mark.parametrize("slug", sorted(QUESTIONS))
def test_the_chat_gives_the_one_dose_and_withdraws_the_old(slug):
    q, name = QUESTIONS[slug]
    b = app._named_product_facts(q)
    assert f"Directions: {DIRECTIONS[slug]}" in b
    rule = [l for l in b.split("\n") if l.startswith("The Directions above are the only dose")]
    assert len(rule) == 1 and WITHDRAWN[slug] in rule[0]
    rest = "\n".join(l for l in b.split("\n") if l not in rule)
    assert WITHDRAWN[slug] not in rest
