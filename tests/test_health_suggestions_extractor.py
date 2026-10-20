"""The portal-chat health-suggestion extractor and the append-on-confirm merge
(Glen's rulings, 20 Oct 2026). The LLM call is stubbed; nothing leaves the box."""
import json

import pytest

from dashboard import health_suggestions as hs


def _llm(items, seen=None):
    def complete(system, user):
        if seen is not None:
            seen.append({"system": system, "user": user})
        return json.dumps({"items": items})
    return complete


def _item(field_id, value, quote, about="self"):
    return {"field_id": field_id, "value": value, "quote": quote, "about": about}


CASES = [
    ("I'm 20 weeks pregnant right now.",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "20 weeks pregnant"),
     "diagnoses", {"diagnosis": "Pregnancy", "current": "Current"}),
    ("I am breastfeeding my 3 month old.",
     _item("diagnoses", {"diagnosis": "Breastfeeding", "current": "Current"}, "I am breastfeeding"),
     "diagnoses", {"diagnosis": "Breastfeeding", "current": "Current"}),
    ("I have epilepsy.",
     _item("diagnoses", {"diagnosis": "Epilepsy", "current": "Current"}, "I have epilepsy"),
     "diagnoses", {"diagnosis": "Epilepsy", "current": "Current"}),
    ("I had atrial fibrillation years ago.",
     _item("diagnoses", {"diagnosis": "Atrial fibrillation", "current": "past"},
           "I had atrial fibrillation years ago"),
     "diagnoses", {"diagnosis": "Atrial fibrillation", "current": "Past"}),
    ("My kidney disease is stage 3.",
     _item("diagnoses", {"diagnosis": "Kidney disease"}, "My kidney disease is stage 3"),
     "diagnoses", {"diagnosis": "Kidney disease", "current": "Current"}),
    ("I have gallstones.",
     _item("diagnoses", {"diagnosis": "Gallstones"}, "I have gallstones"),
     "diagnoses", {"diagnosis": "Gallstones", "current": "Current"}),
    ("They found a blocked bile duct last month.",
     _item("diagnoses", {"diagnosis": "Blocked bile duct"}, "a blocked bile duct"),
     "diagnoses", {"diagnosis": "Blocked bile duct", "current": "Current"}),
    ("I've had asthma since childhood.",
     _item("diagnoses", {"diagnosis": "Asthma"}, "I've had asthma since childhood"),
     "diagnoses", {"diagnosis": "Asthma", "current": "Current"}),
    ("I got a pacemaker at 62 for a slow heartbeat.",
     _item("surgeries", {"procedure": "Pacemaker", "reason": "slow heartbeat", "age": "62"},
           "I got a pacemaker at 62"),
     "surgeries", {"procedure": "Pacemaker", "reason": "slow heartbeat", "age": 62}),
    ("I have a titanium hip implant.",
     _item("surgeries", {"procedure": "Hip implant"}, "titanium hip implant"),
     "surgeries", {"procedure": "Hip implant"}),
    ("I take warfarin as a blood thinner.",
     _item("medications", {"medication": "Warfarin", "reason": "blood thinner"},
           "I take warfarin as a blood thinner"),
     "medications", {"medication": "Warfarin", "reason": "blood thinner"}),
    ("I'm on levothyroxine for my thyroid.",
     _item("medications", {"medication": "Levothyroxine", "reason": "thyroid"},
           "levothyroxine for my thyroid"),
     "medications", {"medication": "Levothyroxine", "reason": "thyroid"}),
    ("I take metformin.",
     _item("medications", {"medication": "Metformin"}, "I take metformin"),
     "medications", {"medication": "Metformin"}),
    ("I take lisinopril for blood pressure.",
     _item("medications", {"medication": "Lisinopril", "reason": "blood pressure"},
           "lisinopril for blood pressure"),
     "medications", {"medication": "Lisinopril", "reason": "blood pressure"}),
    ("I take ibuprofen most days for my back.",
     _item("otc_drugs", {"medication": "Ibuprofen", "reason": "back pain"},
           "I take ibuprofen most days"),
     "otc_drugs", {"medication": "Ibuprofen", "reason": "back pain"}),
    ("I'm allergic to peanuts, my throat itches.",
     _item("allergies", {"sensitivity": "Peanuts", "reaction": "throat itches"},
           "I'm allergic to peanuts"),
     "allergies", {"sensitivity": "Peanuts", "reaction": "throat itches"}),
    ("I follow a low-oxalate diet for my kidneys.",
     _item("diet", "Low-oxalate diet for kidneys", "I follow a low-oxalate diet"),
     "diet", "Low-oxalate diet for kidneys"),
    ("I drink about four beers every night.",
     _item("alcohol", "About four beers every night", "I drink about four beers every night"),
     "alcohol", "About four beers every night"),
]


@pytest.mark.parametrize("msg,item,field,value", CASES)
def test_extractor_maps_each_item(msg, item, field, value):
    out = hs.extract_from_turn(msg, "AI answer", extractor=hs.llm_extractor(_llm([item])))
    assert len(out) == 1
    assert out[0]["field_id"] == field
    assert out[0]["value"] == value
    assert out[0]["rationale"] == f'"{item["quote"]}"'


def test_extractor_sends_only_the_client_message():
    seen = []
    ex = hs.llm_extractor(_llm([], seen))
    hs.extract_from_turn("I take warfarin.", "ANSWER-TEXT-warfarin-is-a-blood-thinner",
                         extractor=ex)
    assert len(seen) == 1
    assert seen[0]["user"] == "I take warfarin."
    assert "ANSWER-TEXT" not in seen[0]["user"] and "ANSWER-TEXT" not in seen[0]["system"]
    assert seen[0]["system"] == hs.EXTRACT_SYSTEM


def test_extractor_drops_facts_taken_from_the_answer():
    """The model 'found' warfarin in the AI answer: its quote is not the client's."""
    item = _item("medications", {"medication": "Warfarin"}, "warfarin is a blood thinner")
    out = hs.extract_from_turn("Is it OK with my other pills?",
                               "Warfarin is a blood thinner, so ask your doctor.",
                               extractor=hs.llm_extractor(_llm([item])))
    assert out == []


def test_extractor_drops_third_parties():
    items = [
        _item("diagnoses", {"diagnosis": "Pregnancy"}, "my daughter is pregnant", about="other"),
        _item("medications", {"medication": "Warfarin"}, "my husband takes warfarin",
              about="husband"),
        _item("alcohol", "drinks a lot", "my dad drinks a lot"),  # about missing below
    ]
    items[2].pop("about")
    out = hs.extract_from_turn("My daughter is pregnant, my husband takes warfarin and "
                               "my dad drinks a lot.", "", extractor=hs.llm_extractor(_llm(items)))
    assert out == []


@pytest.mark.parametrize("reply", [
    "not json", "", "[]", '{"items": "x"}', '{"items": [1, "a", null]}',
    'Here you go: {"items": [{"field_id": "medications", "value": {"medication": "Warfarin"}, '
    '"quote": "I take warfarin", "about": "self"}]}',
    "```json\n{\"items\": [\n",
])
def test_extractor_garbage_returns_empty(reply):
    out = hs.extract_from_turn("I take warfarin.", "",
                               extractor=hs.llm_extractor(lambda s, u: reply))
    assert out == []


def test_extractor_error_returns_empty():
    def boom(s, u):
        raise RuntimeError("down")
    assert hs.extract_from_turn("I take warfarin.", "", extractor=hs.llm_extractor(boom)) == []


def test_extractor_refuses_fields_off_the_list():
    items = [_item("terrain", 3, "I take warfarin"), _item("sleep", "bad", "I take warfarin"),
             _item("first_name", "Zed", "I take warfarin")]
    out = hs.extract_from_turn("I take warfarin.", "", extractor=hs.llm_extractor(_llm(items)))
    assert out == []


def test_extractor_dedupes_within_one_reply():
    item = _item("medications", {"medication": "Warfarin"}, "I take warfarin")
    out = hs.extract_from_turn("I take warfarin.", "",
                               extractor=hs.llm_extractor(_llm([item, dict(item)])))
    assert len(out) == 1


def test_extractor_needs_the_primary_column():
    item = _item("medications", {"reason": "blood"}, "I take warfarin")
    assert hs.extract_from_turn("I take warfarin.", "",
                                extractor=hs.llm_extractor(_llm([item]))) == []


def test_pending_dedupe_respected_for_rows():
    import sqlite3
    cx = sqlite3.connect(":memory:")
    hs.init_table(cx)
    row = {"medication": "Warfarin", "reason": "blood thinner"}
    first = hs.add_pending(cx, "a@b.com", "medications", row, "q", source="chat")
    again = hs.add_pending(cx, "a@b.com", "medications", dict(row), "q", source="chat")
    assert first is not None and again is None
    assert hs.count_pending(cx, "a@b.com") == 1


# --- apply_suggestion: append, never replace -----------------------------------

TABLE_ROWS = {
    "diagnoses": ({"diagnosis": "Asthma", "current": "Past"},
                  {"diagnosis": "Pregnancy", "current": "Current"}),
    "surgeries": ({"procedure": "Appendectomy", "reason": "appendicitis", "age": 12},
                  {"procedure": "Pacemaker", "age": 62}),
    "medications": ({"medication": "Metformin", "reason": "diabetes"},
                    {"medication": "Warfarin", "reason": "blood thinner"}),
    "otc_drugs": ({"medication": "Aspirin"}, {"medication": "Ibuprofen", "reason": "back"}),
    "allergies": ({"sensitivity": "Shellfish", "reaction": "hives"},
                  {"sensitivity": "Peanuts"}),
}


@pytest.mark.parametrize("field", sorted(TABLE_ROWS))
def test_table_suggestion_appends_a_row(field):
    old, new = TABLE_ROWS[field]
    merged = hs.apply_suggestion(field, [old], json.dumps(new))
    assert merged == [old, new]


@pytest.mark.parametrize("field", sorted(TABLE_ROWS))
def test_table_suggestion_skips_an_identical_row(field):
    old, new = TABLE_ROWS[field]
    assert hs.apply_suggestion(field, [old, new], dict(new)) == [old, new]


def test_table_suggestion_to_empty_table():
    assert hs.apply_suggestion("medications", None, {"medication": "Warfarin"}) == [
        {"medication": "Warfarin"}]


def test_table_suggestion_drops_unknown_columns():
    merged = hs.apply_suggestion("medications", [], {"medication": "W", "dose": "5mg"})
    assert merged == [{"medication": "W"}]


def test_table_suggestion_rejects_a_bare_string():
    with pytest.raises(ValueError):
        hs.apply_suggestion("medications", [{"medication": "A"}], "warfarin")


@pytest.mark.parametrize("field", ["diet", "alcohol", "sleep"])
def test_text_suggestion_appends_on_a_new_line(field):
    assert hs.apply_suggestion(field, "Mostly vegetables.", "No gluten") == \
        "Mostly vegetables.\nNo gluten"


def test_text_suggestion_fills_an_empty_field():
    assert hs.apply_suggestion("alcohol", "", "Wine on weekends") == "Wine on weekends"


def test_text_suggestion_skips_text_already_there():
    assert hs.apply_suggestion("diet", "Keto. No gluten.", "no gluten") == "Keto. No gluten."


def test_scale_suggestion_still_replaces():
    assert hs.apply_suggestion("terrain", 2, 4) == 4
