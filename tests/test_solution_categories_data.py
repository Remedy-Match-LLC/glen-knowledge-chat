"""The real solution category file obeys the spec's rules against the real catalogue."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from dashboard import solution_pages as sp

ROOT = Path(__file__).resolve().parent.parent

# Remedy table names that are categories, from e4l.db formulations.category on 2026-09-30
# ('Solution category', 'Tools', 'Tools (laser, tuning fork, helmet)'). EVOX Session is a
# service, so it is out of scope.
TABLE_CATEGORY_NAMES = ["EMF reduction resources", "Fasting resources", "Photobiomodulation",
                        "Infrared & Red Light Photobiomodulation", "Infrared", "172 Hz"]


def _products():
    return json.load(open(ROOT / "data" / "products.json"))["products"]


def test_the_category_file_obeys_every_rule():
    assert sp.problems(sp.load(), _products(), TABLE_CATEGORY_NAMES) == []


def test_there_are_the_ten_approved_categories():
    assert [c["slug"] for c in sp.load()] == [
        "emf-protection", "water-hydrogen", "air", "light-photobiomodulation", "pemf",
        "microcurrent", "frequency-sound", "fasting", "stones-wearables", "books"]


def test_every_category_has_its_copy():
    empty = [c["slug"] for c in sp.load() if not (c.get("principle") or "").strip()]
    assert empty == [], f"no principle written for {empty}"


def test_the_plate_ionizers_are_listed():
    water = next(c for c in sp.load() if c["slug"] == "water-hydrogen")
    assert {"water-ionizer-5plate", "water-ionizer-9plate", "water-ionizer-15plate"} <= set(water["products"])


@pytest.mark.skipif(not os.environ.get("E4L_DB"), reason="set E4L_DB to check against e4l.db")
def test_table_names_match_e4l_db():
    cx = sqlite3.connect(os.environ["E4L_DB"])
    names = {r[0] for r in cx.execute(
        "SELECT name FROM formulations WHERE category IN "
        "('Solution category','Tools','Tools (laser, tuning fork, helmet)')")}
    assert names == set(TABLE_CATEGORY_NAMES)
