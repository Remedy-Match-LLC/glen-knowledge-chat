"""B17 Max's Biofield default dose comes from code, not FileMaker 890 (Glen, 2026-10-25).

FileMaker 890 still says 1 capsule daily with food; B17 Max is now a powder. Glen chose
a code-side override ("code") and approved the three-field split ("yes"), both in the
formulation tab. A fresh snapshot must not bring the capsule back.
"""
import sqlite3

from dashboard import biofield_authoring as ba
from dashboard.biofield_schedule import build_schedule

# One dose a day from 2026-10-31 (Glen, production tab); it had allowed 2 scoops.
TIMING = ("with cooked food. Increase slowly, as guided, up to 1 level scoop once a day. Do "
          "not exceed 1 level scoop a day. If you also take B17 Syntropy, take no more than 1 "
          "capsule a day, and count it as half a scoop toward the 1-scoop limit.")
B17_MAX = {"dosage": "1/4 level scoop", "frequency": "once a day", "timing": TIMING}


def _snapshot():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, dosage TEXT, "
               "dosage_freq TEXT, dosage_timing TEXT)")
    cx.executemany("INSERT INTO fmp_snap_products VALUES (?,?,?,?)", [
        ("B17 Max", "1 capsule", "daily", "with food"),
        ("B17 Syntropy", "1 capsule", "daily", "with food")])
    return cx


def test_b17_max_gets_the_powder_dose_over_the_snapshot():
    assert ba.remedy_dosing(_snapshot(), "B17 Max") == B17_MAX
    assert ba.remedy_dosing(_snapshot(), "b17 max") == B17_MAX


def test_b17_syntropy_keeps_its_snapshot_dose():
    assert ba.remedy_dosing(_snapshot(), "B17 Syntropy") == {
        "dosage": "1 capsule", "frequency": "daily", "timing": "with food"}


def test_a_spoken_dose_still_wins_over_the_override():
    got = ba.merge_dosing("1/2 level scoop", "", "", ba.remedy_dosing(_snapshot(), "B17 Max"))
    assert got == {"dosage": "1/2 level scoop", "frequency": "once a day", "timing": TIMING}


def test_the_schedule_places_it_once_with_food_and_shows_the_steps():
    s = build_schedule([dict(name="B17 Max", **B17_MAX)])
    e = s["entries"][0]
    assert len(e["slots"]) == 1, e
    assert e["per_slot"] == "1/4 level scoop" and e["timing"] == TIMING
    assert e["food"] == "with food", e
