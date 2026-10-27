"""The schedule keeps a timing's condition. Judy Tom's a58 listed Scar Silk and Lipid Zyme
at mid-morning "between meals" and dropped "once the bleeding has stopped" (Glen, "fix
the schedule", 2026-10-26)."""
from dashboard.biofield_report_present import _schedule
from dashboard.biofield_schedule import build_schedule

ROW = {"name": "Scar Silk", "dosage": "1 capsule", "frequency": "daily",
       "timing": "between meals, once the bleeding has stopped"}


def test_the_condition_stays_with_the_food_phrase():
    e = build_schedule([ROW])["entries"][0]
    assert e["food"] == "between meals, once the bleeding has stopped"
    assert e["slots"] == ["Mid-morning"]


def test_a_condition_with_no_food_phrase_still_shows():
    e = build_schedule([{**ROW, "timing": "once the bleeding has stopped"}])["entries"][0]
    assert e["food"] == "once the bleeding has stopped"


def test_a_meal_slot_keeps_its_condition():
    e = build_schedule([{**ROW, "timing": "with breakfast; until the bottle is done."}])["entries"][0]
    assert e["food"] == "with food, until the bottle is done"
    assert e["slots"] == ["Breakfast"]


def test_plain_timings_are_unchanged():
    for t, food in (("between meals", "between meals"), ("with food, or as guided", "with food"),
                    ("with food", "with food"), ("", "")):
        assert build_schedule([{**ROW, "timing": t}])["entries"][0]["food"] == food, t


def test_the_printed_schedule_shows_the_condition():
    html = _schedule({"schedule": build_schedule([ROW])})
    assert "(1 capsule, between meals, once the bleeding has stopped)" in html


def test_a_condition_keeps_the_instruction_it_governs():
    e = build_schedule([{**ROW, "timing": "between meals, if bleeding resumes, stop taking"}])["entries"][0]
    assert e["food"] == "between meals, if bleeding resumes, stop taking"


def test_a_frequency_once_is_not_a_condition():
    for t in ("Once daily with food", "once a day, with food"):
        assert build_schedule([{**ROW, "timing": t}])["entries"][0]["food"] == "with food", t


def test_round_1_cases_print_as_written():
    for t in ("only with food", "between meals, once 1,000 mg is reached",
              "between meals, if bleeding resumes, see Dr. Swartwout"):
        assert build_schedule([{**ROW, "timing": t}])["entries"][0]["food"] == t.rstrip("."), t


def test_only_the_first_sentence_is_read():
    e = build_schedule([{**ROW, "timing": "with food. If you also take B17 Syntropy, count it."}])["entries"][0]
    assert e["food"] == "with food"


def test_a_fallback_keeps_the_food_phrase_its_slot_follows():
    e = build_schedule([{**ROW, "frequency": "twice daily",
                         "timing": "with food; if not, between meals"}])["entries"][0]
    assert e["food"] == "between meals"
    assert e["slots"] == ["Mid-morning", "Mid-afternoon"]


def test_once_followed_by_an_event_is_a_condition():
    e = build_schedule([{**ROW, "timing": "between meals, once a rash clears"}])["entries"][0]
    assert e["food"] == "between meals, once a rash clears"


def test_a_condition_alone_in_the_first_sentence_keeps_the_food_phrase():
    e = build_schedule([{**ROW, "timing": "Once the bleeding has stopped. Take between meals."}])["entries"][0]
    assert e["food"] == "between meals, once the bleeding has stopped"


def test_a_safety_clause_is_kept():
    e = build_schedule([{**ROW, "timing": "between meals, do not take if pregnant"}])["entries"][0]
    assert e["food"] == "between meals, do not take if pregnant"
