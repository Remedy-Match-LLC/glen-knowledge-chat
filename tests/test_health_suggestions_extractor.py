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
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I'm 20 weeks pregnant"),
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
    ("I have a blocked bile duct.",
     _item("diagnoses", {"diagnosis": "Blocked bile duct"}, "I have a blocked bile duct"),
     "diagnoses", {"diagnosis": "Blocked bile duct", "current": "Current"}),
    ("I've had asthma since childhood.",
     _item("diagnoses", {"diagnosis": "Asthma"}, "I've had asthma since childhood"),
     "diagnoses", {"diagnosis": "Asthma", "current": "Current"}),
    ("I got a pacemaker at 62 for a slow heartbeat.",
     _item("surgeries", {"procedure": "Pacemaker", "reason": "slow heartbeat", "age": "62"},
           "I got a pacemaker at 62"),
     "surgeries", {"procedure": "Pacemaker", "reason": "slow heartbeat", "age": 62}),
    ("I have a titanium hip implant.",
     _item("surgeries", {"procedure": "Hip implant"}, "I have a titanium hip implant"),
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
           "I take lisinopril for blood pressure"),
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
     "diet", "I follow a low-oxalate diet"),
    ("I drink about four beers every night.",
     _item("alcohol", "About four beers every night", "I drink about four beers every night"),
     "alcohol", "I drink about four beers every night"),
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


def test_old_shape_plain_string_becomes_a_first_column_row():
    assert hs.apply_suggestion("medications", [{"medication": "A"}], "warfarin") == [
        {"medication": "A"}, {"medication": "warfarin"}]


def test_table_suggestion_with_nothing_usable_raises():
    with pytest.raises(ValueError):
        hs.apply_suggestion("medications", [{"medication": "A"}], {"reason": "x"})


def test_whole_list_clinician_suggestion_appends_each_row():
    merged = hs.apply_suggestion("medications", [{"medication": "A"}],
                                 json.dumps([{"medication": "B"}, {"medication": "A"}]))
    assert merged == [{"medication": "A"}, {"medication": "B"}]


# Round 3 L4: text held in a table field is kept whole as one first-column row,
# never split at commas or lines; a list keeps one row per item.
@pytest.mark.parametrize("current,kept", [
    ("MS, asthma", [{"diagnosis": "MS, asthma"}]),
    ("MS\nasthma", [{"diagnosis": "MS\nasthma"}]),
    (["MS", "asthma"], [{"diagnosis": "MS"}, {"diagnosis": "asthma"}]),
])
def test_table_stored_as_text_is_never_emptied(current, kept):
    merged = hs.apply_suggestion("diagnoses", current, {"diagnosis": "Pregnancy",
                                                        "current": "Current"})
    assert merged == kept + [{"diagnosis": "Pregnancy", "current": "Current"}]


def test_text_table_with_a_comma_in_the_reason_stays_one_row():
    merged = hs.apply_suggestion("medications", "Warfarin 5 mg, for atrial fibrillation",
                                 {"medication": "Metformin"})
    assert merged[0] == {"medication": "Warfarin 5 mg, for atrial fibrillation"}
    assert len(merged) == 2


def test_mixed_list_keeps_rows_and_strings():
    merged = hs.apply_suggestion("medications", [{"medication": "A", "reason": "r"}, "B"],
                                 {"medication": "C"})
    assert merged == [{"medication": "A", "reason": "r"}, {"medication": "B"}, {"medication": "C"}]


@pytest.mark.parametrize("field", ["diet", "alcohol", "sleep"])
def test_text_suggestion_appends_on_a_new_line(field):
    assert hs.apply_suggestion(field, "Mostly vegetables.", "No gluten") == \
        "Mostly vegetables.\nNo gluten"


def test_text_suggestion_fills_an_empty_field():
    assert hs.apply_suggestion("alcohol", "", "Wine on weekends") == "Wine on weekends"


def test_text_suggestion_skips_an_equal_line():
    assert hs.apply_suggestion("diet", "Keto\nNo gluten", "no   GLUTEN") == "Keto\nNo gluten"


def test_text_dedup_is_whole_line_never_substring():
    assert hs.apply_suggestion("alcohol", "12 beers nightly", "2 beers nightly") == \
        "12 beers nightly\n2 beers nightly"
    assert hs.apply_suggestion("diet", "Keto. No gluten.", "no gluten") == \
        "Keto. No gluten.\nno gluten"


def test_scale_suggestion_still_replaces():
    assert hs.apply_suggestion("terrain", 2, 4) == 4


# --- evidence checks (review rounds 1 and 2) -----------------------------------

def _one(msg, item):
    return hs.extract_from_turn(msg, "", extractor=hs.llm_extractor(_llm([item])))


@pytest.mark.parametrize("msg,item", [
    ("My wife is pregnant, can she take this?",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "pregnant")),
    ("My wife is pregnant and I take warfarin.",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "My wife is pregnant")),
    ("My husband and I both take warfarin daily.",
     _item("medications", {"medication": "Warfarin"}, "I both take warfarin daily")),
    ("My daughter has asthma, I worry.",
     _item("diagnoses", {"diagnosis": "Asthma"}, "My daughter has asthma")),
    ("I think I'm fine", _item("diagnoses", {"diagnosis": "Epilepsy"}, "I")),
    ("Is it safe if I am pregnant?", _item("diagnoses", {"diagnosis": "Pregnancy"}, "I am pregnant")),
    ("Taking warfarin daily.", _item("medications", {"medication": "Warfarin"}, "Taking warfarin daily")),
    ("I take warfarin.", _item("medications", {"medication": "Warfarin"}, "warfarin")),
])
def test_unsupported_or_third_party_quotes_are_dropped(msg, item):
    assert _one(msg, item) == []


@pytest.mark.parametrize("msg,quote,diag", [
    ("I'm pregnant with my second baby.", "I'm pregnant with my second baby", "Pregnancy"),
    ("I'm pregnant with my first child.", "I'm pregnant with my first child", "Pregnancy"),
    ("I'm pregnant, expecting our second baby in May.", "I'm pregnant, expecting our second baby",
     "Pregnancy"),
    ("I am breastfeeding my baby.", "I am breastfeeding my baby", "Breastfeeding"),
    ("I'm breastfeeding my youngest kid.", "I'm breastfeeding my youngest kid", "Breastfeeding"),
])
def test_own_pregnancy_or_breastfeeding_with_baby_is_recorded(msg, quote, diag):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, quote))
    assert len(out) == 1, msg
    assert out[0]["value"]["diagnosis"] == diag and out[0]["value"]["current"] == "Current"


@pytest.mark.parametrize("msg,item", [
    ("My baby has a rash.", _item("diagnoses", {"diagnosis": "Rash"}, "My baby has a rash")),
    ("My child has eczema.", _item("diagnoses", {"diagnosis": "Eczema"}, "My child has eczema")),
    ("I'm pregnant with my second baby and my kid has asthma.",
     _item("diagnoses", {"diagnosis": "Asthma"}, "my kid has asthma")),
])
def test_baby_or_child_as_the_subject_stays_third_party(msg, item):
    assert _one(msg, item) == []


def test_table_key_term_must_be_in_the_quote():
    assert _one("I take my pills daily.",
                _item("medications", {"medication": "Warfarin"}, "I take my pills daily")) == []


def test_diet_value_is_the_quote_not_the_paraphrase():
    out = _one("I drink 2 beers nightly.",
               _item("alcohol", "12 beers a night, heavy drinker", "I drink 2 beers nightly"))
    assert out[0]["value"] == "I drink 2 beers nightly"


@pytest.mark.parametrize("msg,quote,diag", [
    ("I was pregnant in 2010.", "I was pregnant in 2010", "Pregnancy"),
    ("I used to breastfeed my kids.", "I used to breastfeed", "Breastfeeding"),
    ("I had a pregnancy years ago.", "I had a pregnancy years ago", "Pregnancy"),
])
def test_past_pregnancy_and_breastfeeding_are_dropped(msg, quote, diag):
    item = _item("diagnoses", {"diagnosis": diag, "current": "Current"}, quote)
    assert _one(msg, item) == []


@pytest.mark.parametrize("msg,quote,given", [
    ("I had asthma as a kid.", "I had asthma as a kid", "Current"),
    ("I had gallstones years ago.", "I had gallstones years ago", None),
    ("I no longer have epilepsy symptoms, my epilepsy is gone.", "I no longer have epilepsy", "Current"),
])
def test_past_tense_diagnosis_is_past_even_when_the_model_says_current(msg, quote, given):
    value = {"diagnosis": {"I had asthma as a kid": "Asthma",
                           "I had gallstones years ago": "Gallstones",
                           "I no longer have epilepsy": "Epilepsy"}[quote]}
    if given:
        value["current"] = given
    out = _one(msg, _item("diagnoses", value, quote))
    assert out and out[0]["value"]["current"] == "Past"


@pytest.mark.parametrize("msg,quote", [
    ("I've had asthma since childhood.", "I've had asthma since childhood"),
    ("I was diagnosed with epilepsy.", "I was diagnosed with epilepsy"),
    ("I have been breastfeeding for a month.", "I have been breastfeeding"),
])
def test_present_facts_stay_current(msg, quote):
    diag = "Asthma" if "asthma" in quote else ("Epilepsy" if "epilepsy" in quote else "Breastfeeding")
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, quote))
    assert out and out[0]["value"]["current"] == "Current"
