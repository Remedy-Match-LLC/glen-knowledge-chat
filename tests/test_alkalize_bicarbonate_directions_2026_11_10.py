"""Alkalize Bicarbonate Blend directions (Glen, 2026-11-10): "Take 1 capsule up to twice
daily, between meals or at bedtime, or as guided." On the schedule, 1 capsule
mid-afternoon and/or 1 at bedtime ("and/or"). FileMaker names the capsule's row "Alkalize Powder"."""
import json
import sqlite3

from dashboard import biofield_authoring as A, biofield_schedule as S

WORDS = "Take 1 capsule up to twice daily, between meals or at bedtime, or as guided."


def test_the_report_dose_comes_from_the_app():
    d = A.remedy_dosing(sqlite3.connect(":memory:"), "Alkalize Bicarbonate Blend")
    assert d == {"dosage": "1 capsule", "frequency": "up to twice daily",
                 "timing": "between meals or at bedtime, or as guided."}


def test_the_schedule_puts_one_capsule_mid_afternoon_and_one_at_bedtime():
    e = S.build_schedule([dict(name="Alkalize Bicarbonate Blend",
                               **A.remedy_dosing(sqlite3.connect(":memory:"),
                                                 "Alkalize Bicarbonate Blend"))])["entries"][0]
    assert e["slots"] == ["Mid-afternoon", "Bedtime"]
    assert e["per_slot"] == "1 capsule"
    assert e["food"].startswith("between meals and/or at bedtime")


def test_a_plain_bedtime_dose_still_goes_together_at_bedtime():
    e = S.build_schedule([{"name": "X", "dosage": "1 capsule", "frequency": "twice daily",
                           "timing": "at bedtime"}])["entries"][0]
    assert e["slots"] == ["Bedtime"]


def test_the_catalogue_carries_the_exact_words_live_and_pinned():
    live = json.load(open("data/products.json"))["products"]["alkalize-bicarbonate-blend"]
    pin = json.load(open("data/products-manual-corrections.json"))["alkalize-bicarbonate-blend"]
    assert live["directions"] == WORDS and pin["directions"] == WORDS
    # A directions-only pin must not mark the formula as Glen-verified: run the real
    # replay and compare the whole record (review round 2).
    import copy
    from scripts.apply_enrichment import apply_corrections
    doc = json.load(open("data/products.json"))["products"]
    before = copy.deepcopy(doc["alkalize-bicarbonate-blend"])
    apply_corrections(doc, json.load(open("data/products-manual-corrections.json")))
    assert doc["alkalize-bicarbonate-blend"] == before
    assert before["ingredients_source"] != "manual"


import pytest


@pytest.mark.parametrize("freq,optional", [
    ("up to twice daily", True), ("up to 2 times daily", True),
    ("once or twice daily", True), ("1 to 2 times daily", True),
    ("twice a day", False), ("twice daily", False), ("2 times daily", False)])
def test_two_doses_with_this_timing_never_stack_at_bedtime(freq, optional):
    e = S.build_schedule([{"name": "X", "dosage": "1 capsule", "frequency": freq,
                           "timing": "between meals or at bedtime, or as guided."}])["entries"][0]
    assert e["slots"] == ["Mid-afternoon", "Bedtime"], freq
    assert e["per_slot"] == "1 capsule"
    assert ("and/or" in e["food"]) is optional, freq
