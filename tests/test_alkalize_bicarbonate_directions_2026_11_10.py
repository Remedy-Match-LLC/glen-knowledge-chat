"""Alkalize Bicarbonate Blend directions (Glen, 2026-11-10): "Take 1 capsule up to twice
daily, between meals or at bedtime, or as guided." On the schedule, 1 capsule
mid-afternoon and/or 1 at bedtime ("and/or"). FileMaker has only Alkalize Powder."""
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
    assert e["food"].startswith("between meals")


def test_a_plain_bedtime_dose_still_goes_together_at_bedtime():
    e = S.build_schedule([{"name": "X", "dosage": "1 capsule", "frequency": "twice daily",
                           "timing": "at bedtime"}])["entries"][0]
    assert e["slots"] == ["Bedtime"]


def test_the_catalogue_carries_the_exact_words_live_and_pinned():
    live = json.load(open("data/products.json"))["products"]["alkalize-bicarbonate-blend"]
    pin = json.load(open("data/products-manual-corrections.json"))["alkalize-bicarbonate-blend"]
    assert live["directions"] == WORDS and pin["directions"] == WORDS
    # A directions-only pin must not mark the formula as Glen-verified.
    assert pin["ingredients_source"] == live["ingredients_source"] != "manual"
