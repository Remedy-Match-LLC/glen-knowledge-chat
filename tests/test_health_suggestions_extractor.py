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
     # "pain" is not in the message, so the model's reason is blanked (live run B).
     "otc_drugs", {"medication": "Ibuprofen"}),
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
    # Scales, budget, identity, the sensitive trauma field and the fixed symptom list
    # are never suggested from chat.
    items = [_item(f, "x", "I take warfarin daily") for f in (
        "terrain", "first_name", "budget_monthly", "commitment", "psychoemotional_trauma",
        "systemic_symptoms", "obstacles", "portrait", "occupation", "terms")]
    # The quote is in the message, so only the field list can refuse them.
    out = hs.extract_from_turn("I take warfarin daily.", "",
                               extractor=hs.llm_extractor(_llm(items)))
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


# Marketing's live run on the real extractor, 20 Oct 2026: a fact stated before a
# question in the same message was dropped. These are the phrasings that failed.
@pytest.mark.parametrize("msg,item,diag_or_med", [
    ("I'm 5 months pregnant, is the cleanse ok for me?",
     _item("diagnoses", {"diagnosis": "Pregnancy", "current": "Current"}, "I'm 5 months pregnant"),
     "Pregnancy"),
    ("I take warfarin, can I use the enzymes?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin"), "Warfarin"),
    ("I have a pacemaker, is the PEMF mat safe?",
     _item("surgeries", {"procedure": "Pacemaker"}, "I have a pacemaker"), "Pacemaker"),
    ("I'm allergic to shellfish, does this have any?",
     _item("allergies", {"sensitivity": "Shellfish"}, "I'm allergic to shellfish"), "Shellfish"),
    ("I'm pregnant. Is the cleanse ok?",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I'm pregnant"), "Pregnancy"),
    ("Hi Dr Glen, I take warfarin and metformin, can I use the enzymes?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin"), "Warfarin"),
    ("I take warfarin so can I use the enzymes?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin"), "Warfarin"),
    ("I'm breastfeeding my son, is this safe?",
     _item("diagnoses", {"diagnosis": "Breastfeeding"}, "I'm breastfeeding my son"), "Breastfeeding"),
    ("Can I take this while I'm on warfarin?",
     _item("medications", {"medication": "Warfarin"}, "I'm on warfarin"), "Warfarin"),
    ("Is the mat safe since I have a pacemaker?",
     _item("surgeries", {"procedure": "Pacemaker"}, "I have a pacemaker"), "Pacemaker"),
    ("I'm breastfeeding my daughter.",
     _item("diagnoses", {"diagnosis": "Breastfeeding"}, "I'm breastfeeding my daughter"),
     "Breastfeeding"),
])
def test_a_fact_before_a_question_is_recorded(msg, item, diag_or_med):
    out = _one(msg, item)
    assert len(out) == 1, msg
    assert diag_or_med in json.dumps(out[0]["value"]), msg


@pytest.mark.parametrize("msg,item", [
    ("Is it ok if I'm 5 months pregnant?",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I'm 5 months pregnant")),
    ("Can I use the enzymes if I take warfarin?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin")),
    ("What if I have a pacemaker, is the mat safe?",
     _item("surgeries", {"procedure": "Pacemaker"}, "I have a pacemaker")),
    ("Could I be pregnant, or is it the cleanse?",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I be pregnant")),
    ("Is it safe if, while I'm pregnant, I fast?",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I'm pregnant")),
    ("So I take warfarin with food?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin")),
    ("Do I take warfarin with food?",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin")),
    ("My son is breastfeeding, is that ok?",
     _item("diagnoses", {"diagnosis": "Breastfeeding"}, "My son is breastfeeding")),
    ("I'm pregnant.", _item("diet", "x", "I'm pregnant")),
])
def test_a_question_or_another_person_still_records_nothing(msg, item):
    assert _one(msg, item) == []


# --- Glen, 20 Oct 2026: anything that can go on the intake form -----------------

def _all(msg, items):
    return hs.extract_from_turn(msg, "", extractor=hs.llm_extractor(_llm(items)))


def test_two_conditions_in_one_sentence_give_two_rows():
    msg = "I have type 2 diabetes and high blood pressure."
    q = "I have type 2 diabetes and high blood pressure"
    out = _all(msg, [_item("diagnoses", {"diagnosis": "Type 2 diabetes"}, q),
                     _item("diagnoses", {"diagnosis": "High blood pressure"}, q)])
    assert [o["value"] for o in out] == [
        {"diagnosis": "Type 2 diabetes", "current": "Current"},
        {"diagnosis": "High blood pressure", "current": "Current"}]


def test_any_past_condition_is_recorded_as_past():
    out = _one("I had shingles in 2019.",
               _item("diagnoses", {"diagnosis": "Shingles", "current": "Current"},
                     "I had shingles in 2019"))
    assert out[0]["value"] == {"diagnosis": "Shingles", "current": "Past"}


NEW_POSITIVE = [
    ("I had my gallbladder out in 2015.",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "I had my gallbladder out in 2015"),
     "surgeries", {"procedure": "Gallbladder removal"}),
    ("I had knee replacement surgery at 58.",
     _item("surgeries", {"procedure": "Knee replacement surgery", "age": 58},
           "I had knee replacement surgery at 58"),
     "surgeries", {"procedure": "Knee replacement surgery", "age": 58}),
    ("I take magnesium glycinate at night.",
     _item("supplements", {"name": "Magnesium glycinate", "reason": "sleep", "need": 9},
           "I take magnesium glycinate at night"),
     # The client never said why, so the model's "sleep" is blanked (live run B).
     "supplements", {"name": "Magnesium glycinate"}),
    ("I take Thorne vitamin D every morning.",
     _item("supplements", {"name": "Vitamin D", "brand": "Thorne"},
           "I take Thorne vitamin D every morning"),
     "supplements", {"name": "Vitamin D", "brand": "Thorne"}),
    ("I take Tylenol for headaches.",
     _item("otc_drugs", {"medication": "Tylenol", "reason": "headaches"},
           "I take Tylenol for headaches"),
     "otc_drugs", {"medication": "Tylenol", "reason": "headaches"}),
    ("I'm on prednisone for my arthritis.",
     _item("medications", {"medication": "Prednisone", "reason": "arthritis"},
           "I'm on prednisone for my arthritis"),
     "medications", {"medication": "Prednisone", "reason": "arthritis"}),
    ("I'm allergic to penicillin, I get hives.",
     _item("allergies", {"sensitivity": "Penicillin", "reaction": "hives"},
           "I'm allergic to penicillin"),
     "allergies", {"sensitivity": "Penicillin", "reaction": "hives"}),
    ("I react badly to mould in my house.",
     _item("allergies", {"sensitivity": "Mould"}, "I react badly to mould"),
     "allergies", {"sensitivity": "Mould"}),
    ("My mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My mother had breast cancer", about="family"),
     "family_history", {"relative": "Mother", "condition": "Breast cancer"}),
    ("My dad got type 2 diabetes at 50.",
     _item("family_history", {"relative": "Father", "condition": "Type 2 diabetes",
                              "age_onset": 50},
           "My dad got type 2 diabetes at 50", about="family"),
     "family_history", {"relative": "Father", "condition": "Type 2 diabetes", "age_onset": 50}),
    ("I want to get rid of my migraines.",
     _item("health_concerns", {"concern": "Migraines", "rating": 10},
           "I want to get rid of my migraines"),
     "health_concerns", {"concern": "Migraines"}),
    ("I've had migraines every week this month.",
     _item("other_symptoms", "Weekly migraines", "I've had migraines every week this month"),
     "other_symptoms", "I've had migraines every week this month"),
    ("I sleep badly, waking at 3 every night.",
     _item("sleep", "Poor sleep", "I sleep badly, waking at 3 every night"),
     "sleep", "I sleep badly, waking at 3 every night"),
    ("I have four amalgam fillings and a root canal.",
     _item("dental", "Amalgams", "I have four amalgam fillings and a root canal"),
     "dental", "I have four amalgam fillings and a root canal"),
    ("I worked with pesticides on a farm for ten years.",
     _item("toxins", "Pesticides", "I worked with pesticides on a farm for ten years"),
     "toxins", "I worked with pesticides on a farm for ten years"),
    ("I broke my wrist in a car accident.",
     _item("physical_trauma", "Broken wrist", "I broke my wrist in a car accident"),
     "physical_trauma", "I broke my wrist in a car accident"),
    ("I had two COVID shots in 2021.",
     _item("vaccinations", "COVID x2", "I had two COVID shots in 2021"),
     "vaccinations", "I had two COVID shots in 2021"),
]


@pytest.mark.parametrize("msg,item,field,value", NEW_POSITIVE)
def test_new_fields_record_the_clients_own_fact(msg, item, field, value):
    out = _one(msg, item)
    assert len(out) == 1, msg
    assert out[0]["field_id"] == field
    assert out[0]["value"] == value
    assert out[0]["rationale"] == f'"{item["quote"]}"'


NEW_QUESTIONS = [
    ("Can I take this if I have type 2 diabetes?",
     _item("diagnoses", {"diagnosis": "Type 2 diabetes"}, "I have type 2 diabetes")),
    ("Is it safe if I had my gallbladder out?",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "I had my gallbladder out")),
    ("Should I take magnesium glycinate at night?",
     _item("supplements", {"name": "Magnesium glycinate"}, "I take magnesium glycinate at night")),
    ("Can I take Tylenol for headaches with this?",
     _item("otc_drugs", {"medication": "Tylenol"}, "I take Tylenol for headaches")),
    ("What if I'm allergic to penicillin?",
     _item("allergies", {"sensitivity": "Penicillin"}, "I'm allergic to penicillin")),
    ("Does breast cancer run in families?",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "breast cancer run in families", about="family")),
    ("Is it worth it if my mother had breast cancer?",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "my mother had breast cancer", about="family")),
    ("Can this help if I want to get rid of my migraines?",
     _item("health_concerns", {"concern": "Migraines"}, "I want to get rid of my migraines")),
    ("Is it normal if I've had migraines every week?",
     _item("other_symptoms", "x", "I've had migraines every week")),
    ("Would this help if I sleep badly every night?",
     _item("sleep", "x", "I sleep badly every night")),
    ("Should I worry if I have amalgam fillings?",
     _item("dental", "x", "I have amalgam fillings")),
    ("Is it a problem if I worked with pesticides?",
     _item("toxins", "x", "I worked with pesticides")),
    ("Does it matter if I broke my wrist?",
     _item("physical_trauma", "x", "I broke my wrist")),
    ("Is it ok if I had two COVID shots?",
     _item("vaccinations", "x", "I had two COVID shots")),
]


@pytest.mark.parametrize("msg,item", NEW_QUESTIONS)
def test_new_fields_record_nothing_from_a_question(msg, item):
    assert _one(msg, item) == [], msg


NEW_THIRD_PARTY = [
    ("My husband has type 2 diabetes.",
     _item("diagnoses", {"diagnosis": "Type 2 diabetes"}, "My husband has type 2 diabetes")),
    ("My friend had her gallbladder out.",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "My friend had her gallbladder out")),
    ("My son takes Tylenol for headaches.",
     _item("otc_drugs", {"medication": "Tylenol"}, "My son takes Tylenol for headaches")),
    ("My daughter is allergic to penicillin.",
     _item("allergies", {"sensitivity": "Penicillin"}, "My daughter is allergic to penicillin")),
    ("My friend's mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My friend's mother had breast cancer", about="family")),
    ("My friend's mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "mother had breast cancer", about="family")),
    ("My friends mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My friends mother had breast cancer", about="family")),
    ("My mother-in-law had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My mother-in-law had breast cancer", about="family")),
    ("My step-father had a stroke.",
     _item("family_history", {"relative": "Father", "condition": "Stroke"},
           "My step-father had a stroke", about="family")),
    ("My step mother had a stroke.",
     _item("family_history", {"relative": "Mother", "condition": "Stroke"},
           "My step mother had a stroke", about="family")),
    ("My husband's mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My husband's mother had breast cancer", about="family")),
    ("My wife's father had a stroke.",
     _item("family_history", {"relative": "Father", "condition": "Stroke"},
           "My wife's father had a stroke", about="family")),
    # Typed without the apostrophe, only the not-family word list catches these.
    ("My husbands mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My husbands mother had breast cancer", about="family")),
    ("My wifes father had a stroke.",
     _item("family_history", {"relative": "Father", "condition": "Stroke"},
           "My wifes father had a stroke", about="family")),
    ("My stepfather had a stroke.",
     _item("family_history", {"relative": "Father", "condition": "Stroke"},
           "My stepfather had a stroke", about="family")),
    ("My husband has heart disease.",
     _item("family_history", {"relative": "Husband", "condition": "Heart disease"},
           "My husband has heart disease", about="family")),
    ("My client's father had a stroke.",
     _item("family_history", {"relative": "Father", "condition": "Stroke"},
           "father had a stroke", about="family")),
    ("My husband wants to get rid of his migraines.",
     _item("health_concerns", {"concern": "Migraines"},
           "My husband wants to get rid of his migraines")),
    ("My daughter has had migraines every week.",
     _item("other_symptoms", "x", "My daughter has had migraines every week")),
    ("My partner sleeps badly every night.",
     _item("sleep", "x", "My partner sleeps badly every night")),
    ("My kid has three amalgam fillings, I think.",
     _item("dental", "x", "My kid has three amalgam fillings")),
    ("My husband worked with pesticides for years.",
     _item("toxins", "x", "My husband worked with pesticides for years")),
    ("My brother broke my wrist wrestling.",
     _item("physical_trauma", "x", "My brother broke my wrist wrestling")),
    # Typed without the apostrophe, the possessive is still someone else's.
    ("My husbands doctor has him on warfarin.",
     _item("medications", {"medication": "Warfarin"}, "My husbands doctor has him on warfarin")),
    ("My moms diabetes is getting worse.",
     _item("diagnoses", {"diagnosis": "Diabetes"}, "My moms diabetes is getting worse")),
    ("My mother had two COVID shots.",
     _item("vaccinations", "x", "My mother had two COVID shots")),
]


@pytest.mark.parametrize("msg,item", NEW_THIRD_PARTY)
def test_new_fields_record_nothing_about_another_person(msg, item):
    assert _one(msg, item) == [], msg


@pytest.mark.parametrize("msg,item", [
    # The family field still needs about=family, and other fields still need self.
    ("My mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My mother had breast cancer")),
    # A relative's fact never lands on the client's own diagnoses.
    ("My mother had breast cancer.",
     _item("diagnoses", {"diagnosis": "Breast cancer"}, "My mother had breast cancer",
           about="family")),
    ("My mother had breast cancer.",
     _item("diagnoses", {"diagnosis": "Breast cancer"}, "My mother had breast cancer")),
    # The relative in the value must be the one in the quote.
    ("My mother had breast cancer.",
     _item("family_history", {"relative": "Sister", "condition": "Breast cancer"},
           "My mother had breast cancer", about="family")),
    ("My mother had breast cancer.",
     _item("family_history", {"relative": "", "condition": "Breast cancer"},
           "My mother had breast cancer", about="family")),
    # The condition must be in the quote.
    ("My mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Ovarian cancer"},
           "My mother had breast cancer", about="family")),
    # A goal is something the client wants, not just a symptom named.
    ("I get migraines.", _item("health_concerns", {"concern": "Migraines"}, "I get migraines")),
    # A symptom or sleep trouble the client says is over is not recorded.
    ("I used to get migraines every week.",
     _item("other_symptoms", "x", "I used to get migraines every week")),
    ("I had trouble sleeping years ago.",
     _item("sleep", "x", "I had trouble sleeping years ago")),
    # A procedure made only of generic words has no evidence.
    ("I had surgery last year.", _item("surgeries", {"procedure": "Surgery"}, "I had surgery")),
])
def test_new_field_evidence_checks(msg, item):
    assert _one(msg, item) == [], msg


def test_our_mother_is_the_clients_own_family():
    out = _one("Our mother had breast cancer.",
               _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
                     "Our mother had breast cancer", about="family"))
    assert out and out[0]["value"]["relative"] == "Mother"


def test_my_mom_supports_relative_mother():
    out = _one("My mom has Alzheimer's.",
               _item("family_history", {"relative": "Mother", "condition": "Alzheimer's"},
                     "My mom has Alzheimer's", about="family"))
    assert out and out[0]["value"] == {"relative": "Mother", "condition": "Alzheimer's"}


def test_an_age_the_client_never_gave_is_dropped():
    out = _one("I had my appendix out as a teenager.",
               _item("surgeries", {"procedure": "Appendix removal", "age": 15},
                     "I had my appendix out as a teenager"))
    assert out and out[0]["value"] == {"procedure": "Appendix removal"}
    out = _one("My mother had breast cancer.",
               _item("family_history", {"relative": "Mother", "condition": "Breast cancer",
                                        "age_onset": 52},
                     "My mother had breast cancer", about="family"))
    assert out and "age_onset" not in out[0]["value"]


def test_a_relative_must_be_the_clients_own():
    assert _one("I know a mother who had breast cancer.",
                _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
                      "I know a mother who had breast cancer", about="family")) == []


def test_own_mother_beside_a_friends_mother_still_records():
    out = _one("My mother had breast cancer, and so did my friend's mom.",
               _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
                     "My mother had breast cancer", about="family"))
    assert len(out) == 1


def test_a_relatives_condition_mentioned_with_a_friend_still_records():
    out = _one("My mother had breast cancer, like my friend.",
               _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
                     "My mother had breast cancer", about="family"))
    assert len(out) == 1


NEW_ROWS = {
    "supplements": ({"name": "Fish oil", "brand": "Nordic"}, {"name": "Magnesium glycinate"}),
    "family_history": ({"relative": "Father", "condition": "Stroke"},
                       {"relative": "Mother", "condition": "Breast cancer"}),
    "health_concerns": ({"concern": "Fatigue", "rating": 8}, {"concern": "Migraines"}),
}


@pytest.mark.parametrize("field", sorted(NEW_ROWS))
def test_new_table_suggestion_appends_a_row(field):
    old, new = NEW_ROWS[field]
    assert hs.apply_suggestion(field, [old], json.dumps(new)) == [old, new]


@pytest.mark.parametrize("field", list(hs.TEXT_FIELDS))
def test_every_text_field_appends_a_line(field):
    assert hs.apply_suggestion(field, "Earlier note.", "New note") == "Earlier note.\nNew note"


def test_every_extract_field_is_an_editable_intake_field():
    from dashboard import health_profile, intake
    assert set(hs.EXTRACT_FIELDS) <= health_profile.EDITABLE_FIELD_IDS
    forms = {f["id"]: f for f in intake._fields()}
    for fid, cols in hs.TABLE_COLUMNS.items():
        assert forms[fid]["type"] == "table", fid
        assert set(cols) <= {c["id"] for c in forms[fid]["columns"]}, fid
    for fid in hs.TEXT_FIELDS:
        assert forms[fid]["type"] == "textarea", fid


def test_a_supplement_without_a_brand_is_a_row():
    assert hs.apply_suggestion("supplements", [], {"name": "Magnesium glycinate"}) == [
        {"name": "Magnesium glycinate"}]
    assert hs.apply_suggestion("supplements", None, "Vitamin C") == [{"name": "Vitamin C"}]
    with pytest.raises(ValueError):
        hs.apply_suggestion("supplements", [], {"brand": "Thorne"})


def test_a_diagnosis_keeps_a_stated_age_of_onset():
    out = _one("I was diagnosed with lupus at 34.",
               _item("diagnoses", {"diagnosis": "Lupus", "age_onset": "34"},
                     "I was diagnosed with lupus at 34"))
    assert out and out[0]["value"] == {"diagnosis": "Lupus", "current": "Current", "age_onset": 34}



# --- fix round 1 (marketing live run, review rounds 1 and 2) --------------------

def test_prompt_says_a_fact_inside_a_question_is_a_fact():
    """Live run A: the model called "while I'm on warfarin?" only a question."""
    p = hs.EXTRACT_SYSTEM
    assert "A fact the client states inside a question is still a fact." in p
    assert "Can I take this while I'm on warfarin?" in p
    assert "Only a fact under if, whether, suppose or what if is hypothetical" in p


def test_prompt_says_leave_unsaid_columns_blank():
    assert "Leave a value column blank unless the client said it" in hs.EXTRACT_SYSTEM


def test_prompt_trauma_line_is_emotional_only():
    p = hs.EXTRACT_SYSTEM
    assert "emotional trauma" in p and "never record feelings, trauma" not in p
    assert "Physical injuries are recorded" in p


def test_prompt_first_person_matches_the_code():
    p = hs.EXTRACT_SYSTEM
    assert "with I or my (my or our for family history)" in p
    assert "including I, my or our" not in p


@pytest.mark.parametrize("msg,quote", [
    ("Can I take this while I'm on warfarin?", "I'm on warfarin"),
    ("Can I take this while I'm on warfarin?", "while I'm on warfarin"),
    ("Is it ok because I take warfarin?", "I take warfarin"),
])
def test_a_fact_stated_inside_a_question_records(msg, quote):
    out = _one(msg, _item("medications", {"medication": "Warfarin"}, quote))
    assert out and out[0]["value"] == {"medication": "Warfarin"}, msg


# B / round 1 M4 / round 2: secondary cells must be in the client's message.
@pytest.mark.parametrize("msg,item,expect", [
    ("I take magnesium glycinate at night.",
     _item("supplements", {"name": "Magnesium glycinate", "reason": "sleep", "brand": "Thorne"},
           "I take magnesium glycinate at night"),
     {"name": "Magnesium glycinate"}),
    ("I take magnesium glycinate for sleep.",
     _item("supplements", {"name": "Magnesium glycinate", "reason": "sleep"},
           "I take magnesium glycinate for sleep"),
     {"name": "Magnesium glycinate", "reason": "sleep"}),
    ("I'm allergic to penicillin.",
     _item("allergies", {"sensitivity": "Penicillin", "reaction": "hives"},
           "I'm allergic to penicillin"),
     {"sensitivity": "Penicillin"}),
    ("I take warfarin.",
     _item("medications", {"medication": "Warfarin", "reason": "atrial fibrillation"},
           "I take warfarin"),
     {"medication": "Warfarin"}),
])
def test_unsaid_secondary_cells_are_blanked(msg, item, expect):
    out = _one(msg, item)
    assert out and out[0]["value"] == expect, msg


# Round 2: short and significant words in a term must be said.
@pytest.mark.parametrize("msg,item", [
    ("I take vitamin C daily.", _item("supplements", {"name": "Vitamin D"}, "I take vitamin C daily")),
    ("I take B12 shots.", _item("supplements", {"name": "B6"}, "I take B12 shots")),
    ("I take vitamin D3.", _item("supplements", {"name": "Vitamin D2"}, "I take vitamin D3")),
    ("I had knee surgery.", _item("surgeries", {"procedure": "Knee replacement"}, "I had knee surgery")),
    ("I had my gallbladder looked at.",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "I had my gallbladder looked at")),
])
def test_significant_words_of_the_term_must_be_said(msg, item):
    assert _one(msg, item) == [], msg


@pytest.mark.parametrize("msg,item,name", [
    ("I take vitamin C daily.", _item("supplements", {"name": "Vitamin C"}, "I take vitamin C daily"),
     "Vitamin C"),
    ("I take B12 shots.", _item("supplements", {"name": "B12"}, "I take B12 shots"), "B12"),
    ("I had my knee operated on.", _item("surgeries", {"procedure": "Knee surgery"},
                                         "I had my knee operated on"), "Knee surgery"),
    ("My appendix was removed at 12.",
     _item("surgeries", {"procedure": "Appendix removal", "age": 12}, "My appendix was removed at 12"),
     "Appendix removal"),
])
def test_said_terms_still_record(msg, item, name):
    out = _one(msg, item)
    assert out and name in out[0]["value"].values(), msg


# Round 2 and round 1 L1: an age needs age wording, not just the number.
@pytest.mark.parametrize("msg,item,kept", [
    ("I had knee replacement surgery. I take 50 mg of medication daily.",
     _item("surgeries", {"procedure": "Knee replacement surgery", "age": 50},
           "I had knee replacement surgery"), False),
    ("I have had diabetes for 10 years.",
     _item("diagnoses", {"diagnosis": "Diabetes", "age_onset": 10},
           "I have had diabetes for 10 years"), False),
    ("I was diagnosed with lupus at age 34.",
     _item("diagnoses", {"diagnosis": "Lupus", "age_onset": 34}, "I was diagnosed with lupus"), True),
    ("I got asthma when I was 7.",
     _item("diagnoses", {"diagnosis": "Asthma", "age_onset": 7}, "I got asthma"), True),
    ("I had my tonsils out, I was 9 years old.",
     _item("surgeries", {"procedure": "Tonsils removal", "age": 9}, "I had my tonsils out"), True),
])
def test_an_age_needs_age_wording(msg, item, kept):
    out = _one(msg, item)
    assert out, msg
    col = "age" if "age" in item["value"] else "age_onset"
    assert (col in out[0]["value"]) is kept, msg


@pytest.mark.parametrize("age", ["inf", "1e999", "nan", "-5", "400"])
def test_a_bad_age_is_blanked_never_raises(age):
    assert hs.normalize_row("surgeries", {"procedure": "Pacemaker", "age": age}) == {
        "procedure": "Pacemaker"}


# Round 1 M5 and round 2: only allowed words between "my" and the relative.
@pytest.mark.parametrize("msg", [
    "My adoptive mother had breast cancer.", "My foster mother had breast cancer.",
    "My doctors mother had breast cancer.", "My godmother had breast cancer.",
    "My step-mother had breast cancer.", "My ex mother had breast cancer.",
])
def test_non_blood_or_other_peoples_relatives_are_refused(msg):
    q = msg.rstrip(".")
    item = _item("family_history", {"relative": "Mother", "condition": "Breast cancer"}, q,
                 about="family")
    assert _one(msg, item) == [], msg


@pytest.mark.parametrize("msg,relative", [
    ("My late mother had breast cancer.", "Mother"),
    ("My older sister had breast cancer.", "Sister"),
    ("My maternal grandmother had breast cancer.", "Maternal grandmother"),
    ("My biological father had breast cancer.", "Father"),
    ("My half-brother had breast cancer.", "Brother"),
])
def test_allowed_family_modifiers_record(msg, relative):
    q = msg.rstrip(".")
    out = _one(msg, _item("family_history", {"relative": relative, "condition": "Breast cancer"},
                          q, about="family"))
    assert out and out[0]["value"]["relative"] == relative, msg


# Round 1 M6: the relative must be the chain's last link, named exactly.
@pytest.mark.parametrize("msg,relative,ok", [
    ("My mother's brother had colon cancer.", "Mother", False),
    ("My mother's brother had colon cancer.", "Brother", False),
    ("My mother's brother had colon cancer.", "Maternal uncle", True),
    ("My mother's brother had colon cancer.", "Uncle", True),
    ("My mother's brother had colon cancer.", "Paternal uncle", False),
    ("My mom's dad had colon cancer.", "Grandfather", True),
    ("My mom's dad had colon cancer.", "Father", False),
    ("My grandmother had colon cancer.", "Maternal grandmother", False),
    ("My grandmother on my mother's side had colon cancer.", "Maternal grandmother", True),
    ("My mother had colon cancer.", "Mother and aunt", False),
])
def test_relative_matches_the_chain_exactly(msg, relative, ok):
    q = msg.rstrip(".")
    out = _one(msg, _item("family_history", {"relative": relative, "condition": "Colon cancer"},
                          q, about="family"))
    assert bool(out) is ok, (msg, relative)


# Round 2: quoted speech is someone else's words.
@pytest.mark.parametrize("msg,item", [
    ('My friend said, "My mother had breast cancer".',
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My mother had breast cancer", about="family")),
    ("My friend told me my mother had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "my mother had breast cancer", about="family")),
    ('She wrote: "I take warfarin daily".',
     _item("medications", {"medication": "Warfarin"}, "I take warfarin daily")),
    ("He said, I have diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "I have diabetes")),
])
def test_quoted_speech_records_nothing(msg, item):
    assert _one(msg, item) == [], msg


def test_reported_speech_about_the_client_records():
    """Indirect speech keeps the client's own "I": the doctor told the client."""
    out = _one("My doctor told me I have hypothyroidism.",
               _item("diagnoses", {"diagnosis": "Hypothyroidism"}, "I have hypothyroidism"))
    assert out and out[0]["value"]["current"] == "Current"


# Round 1 H1: a stopped medicine, supplement or OTC drug is not taken now.
@pytest.mark.parametrize("msg,field,col,name", [
    ("I stopped taking warfarin last year.", "medications", "medication", "Warfarin"),
    ("I used to take fish oil.", "supplements", "name", "Fish oil"),
    ("I no longer take Tylenol.", "otc_drugs", "medication", "Tylenol"),
    ("I came off metformin in March.", "medications", "medication", "Metformin"),
    ("I quit taking ibuprofen.", "otc_drugs", "medication", "Ibuprofen"),
    ("I was on prednisone in 2019.", "medications", "medication", "Prednisone"),
])
def test_a_stopped_medicine_or_supplement_records_nothing(msg, field, col, name):
    assert _one(msg, _item(field, {col: name}, msg.rstrip("."))) == [], msg


def test_a_medicine_started_in_a_past_year_is_current():
    out = _one("I started metformin in 2020.",
               _item("medications", {"medication": "Metformin"}, "I started metformin in 2020"))
    assert out and out[0]["value"] == {"medication": "Metformin"}


# Round 1 H2: a date of diagnosis does not make it Past.
@pytest.mark.parametrize("msg,diag", [
    ("I was diagnosed with diabetes last year.", "Diabetes"),
    ("I was diagnosed with Hashimoto's in 2019.", "Hashimoto's"),
    ("I was told I have hypothyroidism.", "Hypothyroidism"),
    ("I had a hip replacement and now have arthritis.", "Arthritis"),
])
def test_dated_diagnosis_stays_current(msg, diag):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag, "current": "Current"}, msg.rstrip(".")))
    assert out and out[0]["value"]["current"] == "Current", msg


@pytest.mark.parametrize("msg,diag", [
    ("I was diagnosed with cancer in 2010 and it was cured.", "Cancer"),
    ("I had hepatitis in 2015, it resolved.", "Hepatitis"),
])
def test_an_ended_diagnosis_is_past(msg, diag):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, msg.rstrip(".")))
    assert out and out[0]["value"]["current"] == "Past", msg


# Round 1 M1: negation, suspicion, plans and a leading "if" are not facts.
@pytest.mark.parametrize("msg,item", [
    ("I don't have diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "I don't have diabetes")),
    ("I'm not allergic to penicillin.",
     _item("allergies", {"sensitivity": "Penicillin"}, "I'm not allergic to penicillin")),
    ("I think I might have Lyme disease.",
     _item("diagnoses", {"diagnosis": "Lyme disease"}, "I might have Lyme disease")),
    ("I think I have Lyme disease.",
     _item("diagnoses", {"diagnosis": "Lyme disease"}, "I have Lyme disease")),
    ("Maybe I have IBS.", _item("diagnoses", {"diagnosis": "IBS"}, "I have IBS")),
    ("I'm not sure if I have Lyme disease.",
     _item("diagnoses", {"diagnosis": "Lyme disease"}, "I have Lyme disease")),
    ("I'm trying to get pregnant.",
     _item("diagnoses", {"diagnosis": "Pregnancy"}, "I'm trying to get pregnant")),
    ("I'm having my gallbladder out next month.",
     _item("surgeries", {"procedure": "Gallbladder removal"},
           "I'm having my gallbladder out next month")),
    ("I need a hip replacement.",
     _item("surgeries", {"procedure": "Hip replacement"}, "I need a hip replacement")),
    ("My doctor wants me to start metformin.",
     _item("medications", {"medication": "Metformin"}, "wants me to start metformin")),
    ("I'm going to start metformin.",
     _item("medications", {"medication": "Metformin"}, "I'm going to start metformin")),
    ("If I had diabetes I would cut sugar.",
     _item("diagnoses", {"diagnosis": "Diabetes"}, "I had diabetes")),
    ("I'm thinking of getting the flu shot.",
     _item("vaccinations", "x", "I'm thinking of getting the flu shot")),
    ("My mother never had breast cancer.",
     _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
           "My mother never had breast cancer", about="family")),
])
def test_negation_suspicion_plans_and_if_record_nothing(msg, item):
    assert _one(msg, item) == [], msg


def test_not_vaccinated_is_a_real_vaccinations_answer():
    out = _one("I am not vaccinated.", _item("vaccinations", "x", "I am not vaccinated"))
    assert out and out[0]["value"] == "I am not vaccinated"


# Round 1 M2: the client's own fact with a relative in the sentence still records.
@pytest.mark.parametrize("msg,quote,diag", [
    ("I have high blood pressure like my dad.", "I have high blood pressure", "High blood pressure"),
    ("I have diabetes, it runs in my family and my mother had it too.", "I have diabetes", "Diabetes"),
    ("My mother and I both have diabetes.", "I both have diabetes", "Diabetes"),
])
def test_own_fact_beside_a_relative_records(msg, quote, diag):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, quote))
    assert out and out[0]["value"]["diagnosis"] == diag, msg


@pytest.mark.parametrize("msg,quote", [
    ("My husband and I both take warfarin daily.", "I both take warfarin daily"),
    ("My wife and I take magnesium glycinate.", "I take magnesium glycinate"),
])
def test_a_joint_statement_records_for_the_client(msg, quote):
    field = "supplements" if "magnesium" in msg else "medications"
    col = "name" if field == "supplements" else "medication"
    term = "Magnesium glycinate" if field == "supplements" else "Warfarin"
    assert len(_one(msg, _item(field, {col: term}, quote))) == 1, msg


def test_own_fact_and_family_fact_from_one_sentence():
    msg = "My mother and I both have diabetes."
    out = _all(msg, [
        _item("diagnoses", {"diagnosis": "Diabetes"}, "I both have diabetes"),
        _item("family_history", {"relative": "Mother", "condition": "Diabetes"},
              "My mother and I both have diabetes", about="family")])
    assert [o["field_id"] for o in out] == ["diagnoses", "family_history"]


# Round 1 M3: a dated start of a current symptom or sleep problem stays.
@pytest.mark.parametrize("msg,field", [
    ("I've had headaches since I was in a car accident.", "other_symptoms"),
    ("I started getting migraines in 2020 and still get them.", "other_symptoms"),
    ("I have had trouble sleeping since my surgery last year.", "sleep"),
])
def test_dated_start_of_a_current_symptom_records(msg, field):
    out = _one(msg, _item(field, "x", msg.rstrip(".")))
    assert out and out[0]["value"] == msg.rstrip("."), msg


# Round 1 L5: an anti-goal or a wish to know is not a health concern.
@pytest.mark.parametrize("msg,quote,concern", [
    ("I don't want to take metformin.", "I don't want to take metformin", "Take metformin"),
    ("I want to know if I have diabetes.", "I want to know if I have diabetes", "Diabetes"),
])
def test_anti_goals_are_not_concerns(msg, quote, concern):
    assert _one(msg, _item("health_concerns", {"concern": concern}, quote)) == [], msg



@pytest.mark.parametrize("msg,item,ok", [
    ("I take CoQ100 daily.", _item("supplements", {"name": "CoQ10"}, "I take CoQ100 daily"), False),
    ("I take CoQ10 daily.", _item("supplements", {"name": "CoQ10"}, "I take CoQ10 daily"), True),
])
def test_a_word_with_a_digit_must_be_said_exactly(msg, item, ok):
    assert bool(_one(msg, item)) is ok, msg


@pytest.mark.parametrize("msg,item,kept", [
    ("I went in at 8 pm and had my appendix out.",
     _item("surgeries", {"procedure": "Appendix removal", "age": 8}, "had my appendix out"), False),
    ("I was 40 when I had my hysterectomy.",
     _item("surgeries", {"procedure": "Hysterectomy", "age": 40}, "I had my hysterectomy"), True),
])
def test_age_wording_edges(msg, item, kept):
    out = _one(msg, item)
    assert out and ("age" in out[0]["value"]) is kept, msg


def test_a_quote_inside_quotation_marks_records_nothing():
    assert _one('I read "I take warfarin daily" on a forum.',
                _item("medications", {"medication": "Warfarin"}, "I take warfarin daily")) == []


def test_the_client_quoting_themself_records():
    out = _one("I said, I take warfarin daily.",
               _item("medications", {"medication": "Warfarin"}, "I take warfarin daily"))
    assert len(out) == 1


@pytest.mark.parametrize("msg,diag,status", [
    ("I had shingles in 2019 and I am fine now.", "Shingles", "Past"),
    ("I stopped smoking and I have asthma.", "Asthma", "Current"),
])
def test_the_clause_naming_the_diagnosis_decides(msg, diag, status):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, msg.rstrip(".")))
    assert out and out[0]["value"]["current"] == status, msg


@pytest.mark.parametrize("msg", [
    "My mother's friend had breast cancer.", "My mother-in-law had breast cancer.",
    "My mothers friend had breast cancer.",
])
def test_a_chain_that_goes_on_to_someone_else_is_refused(msg):
    item = _item("family_history", {"relative": "Mother", "condition": "Breast cancer"},
                 msg.rstrip("."), about="family")
    assert _one(msg, item) == [], msg



@pytest.mark.parametrize("msg,item,kept", [
    ("I got asthma at 5 years of age.",
     _item("diagnoses", {"diagnosis": "Asthma", "age_onset": 5}, "I got asthma"), True),
    ("I was 6 months pregnant when I had my appendix out.",
     _item("surgeries", {"procedure": "Appendix removal", "age": 6}, "I had my appendix out"), False),
])
def test_age_wording_years_and_months(msg, item, kept):
    out = _one(msg, item)
    col = "age" if "age" in item["value"] else "age_onset"
    assert out and (col in out[0]["value"]) is kept, msg


def test_a_value_naming_two_relatives_is_refused():
    msg = "My mother and my aunt had colon cancer."
    out = _one(msg, _item("family_history", {"relative": "Mother and aunt",
                                             "condition": "Colon cancer"},
                          "My mother and my aunt had colon cancer", about="family"))
    assert out == []


@pytest.mark.parametrize("msg,field", [
    ("I got migraines in 2019 and still get them.", "other_symptoms"),
    ("My sleep got bad in 2019 and since then I wake at 3.", "sleep"),
])
def test_still_or_since_keeps_a_dated_symptom(msg, field):
    out = _one(msg, _item(field, "x", msg.rstrip(".")))
    assert out, msg


# --- fix round 2 (marketing live run on 0d5d4dff, review round 3) ---------------

def test_live_run_conjoined_fact_inherits_the_clients_subject():
    """Haiku returned both items; the second quote had no "I" and was dropped."""
    msg = "I have type 2 diabetes and high blood pressure."
    out = _all(msg, [_item("diagnoses", {"diagnosis": "Type 2 diabetes"}, "I have type 2 diabetes"),
                     _item("diagnoses", {"diagnosis": "High blood pressure"}, "high blood pressure")])
    assert [o["value"]["diagnosis"] for o in out] == ["Type 2 diabetes", "High blood pressure"]


@pytest.mark.parametrize("msg,quote,ok", [
    ("I have diabetes and asthma.", "asthma", True),
    ("I have diabetes, asthma.", "asthma", True),
    ("I have diabetes plus asthma.", "asthma", True),
    ("I have diabetes as well as asthma.", "asthma", True),
    ("I have diabetes and also asthma.", "asthma", True),
    ("My husband has diabetes and asthma.", "asthma", False),
    ("I don't have diabetes and asthma.", "asthma", False),
    ("If I have diabetes and asthma, is it ok?", "asthma", False),
    ("I have diabetes but asthma runs in the family.", "asthma runs in the family", False),
    ("Asthma is common.", "Asthma is common", False),
    ("I have diabetes. Asthma too.", "Asthma too", False),
])
def test_conjoined_fact_rules(msg, quote, ok):
    out = _one(msg, _item("diagnoses", {"diagnosis": "Asthma"}, quote))
    assert bool(out) is ok, msg


def test_prompt_names_blood_relatives():
    p = hs.EXTRACT_SYSTEM
    assert ("Blood relatives are parents, children, siblings, half-siblings, grandparents, "
            "grandchildren, aunts, uncles, nieces, nephews and cousins.") in p
    assert "A parent's sibling (\"my mother's brother\" is an uncle)" in p
    assert "a parent's parent" in p and "IS a blood relative" in p
    assert ("Spouses, partners, in-laws, step-relatives and adoptive relatives are NOT "
            "blood relatives") in p


def test_mothers_brother_records_as_maternal_uncle():
    out = _one("My mother's brother had colon cancer.",
               _item("family_history", {"relative": "Maternal uncle", "condition": "Colon cancer"},
                     "My mother's brother had colon cancer", about="family"))
    assert out and out[0]["value"]["relative"] == "Maternal uncle"


# H1: the other-person list.
@pytest.mark.parametrize("msg,item", [
    ("My dog has arthritis.", _item("diagnoses", {"diagnosis": "Arthritis"}, "My dog has arthritis")),
    ("I give my dog turmeric every day.",
     _item("supplements", {"name": "Turmeric"}, "I give my dog turmeric every day")),
    ("My cat is allergic to chicken.",
     _item("allergies", {"sensitivity": "Chicken"}, "My cat is allergic to chicken")),
    ("My spouse has diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "My spouse has diabetes")),
    ("My parents have diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "My parents have diabetes")),
    ("My roommate takes Xanax.", _item("medications", {"medication": "Xanax"}, "My roommate takes Xanax")),
    ("My colleague takes metformin.",
     _item("medications", {"medication": "Metformin"}, "My colleague takes metformin")),
    ("My toddler has eczema.", _item("diagnoses", {"diagnosis": "Eczema"}, "My toddler has eczema")),
    ("My grandkids have asthma.", _item("diagnoses", {"diagnosis": "Asthma"}, "My grandkids have asthma")),
    ("My stepson has autism.", _item("diagnoses", {"diagnosis": "Autism"}, "My stepson has autism")),
    ("He has diabetes and I worry.", _item("diagnoses", {"diagnosis": "Diabetes"}, "He has diabetes and I worry")),
    ("She had a hysterectomy and I'm next.",
     _item("surgeries", {"procedure": "Hysterectomy"}, "She had a hysterectomy and I'm next")),
    ("My hubby has gout.", _item("diagnoses", {"diagnosis": "Gout"}, "My hubby has gout")),
    ("My fiance has asthma.", _item("diagnoses", {"diagnosis": "Asthma"}, "My fiance has asthma")),
    ("My boss takes statins.", _item("medications", {"medication": "Statins"}, "My boss takes statins")),
    ("My sibling has celiac.", _item("diagnoses", {"diagnosis": "Celiac"}, "My sibling has celiac")),
    ("My teenager has acne.", _item("diagnoses", {"diagnosis": "Acne"}, "My teenager has acne")),
    ("My grandchildren have eczema.",
     _item("diagnoses", {"diagnosis": "Eczema"}, "My grandchildren have eczema")),
    ("My sister-in-law has lupus.", _item("diagnoses", {"diagnosis": "Lupus"}, "My sister-in-law has lupus")),
    ("My puppy has allergies to chicken.",
     _item("allergies", {"sensitivity": "Chicken"}, "My puppy has allergies to chicken")),
    # Round 3 mutation M20: these four were untested.
    ("My neighbour has diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "My neighbour has diabetes")),
    ("My coworker takes metformin.",
     _item("medications", {"medication": "Metformin"}, "My coworker takes metformin")),
    ("My client has asthma.", _item("diagnoses", {"diagnosis": "Asthma"}, "My client has asthma")),
    ("My patient takes warfarin.", _item("medications", {"medication": "Warfarin"}, "My patient takes warfarin")),
])
def test_other_people_and_pets_record_nothing(msg, item):
    assert _one(msg, item) == [], msg


# H2: the text after the quote is read.
@pytest.mark.parametrize("msg,item", [
    ("I take aspirin but stopped last week.", _item("otc_drugs", {"medication": "Aspirin"}, "I take aspirin")),
    ("I take warfarin, well I did until March.",
     _item("medications", {"medication": "Warfarin"}, "I take warfarin")),
    ("My doctor prescribed metformin but I haven't started it.",
     _item("medications", {"medication": "Metformin"}, "My doctor prescribed metformin")),
    ("I have diabetes, or at least my doctor thinks so.",
     _item("diagnoses", {"diagnosis": "Diabetes"}, "I have diabetes")),
    ("I'm getting my gallbladder out next month.",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "I'm getting my gallbladder out")),
    ("I'm getting my gallbladder out next month.",
     _item("surgeries", {"procedure": "Gallbladder removal"}, "my gallbladder out")),
    ("I'm having surgery on my shoulder in May.",
     _item("surgeries", {"procedure": "Shoulder surgery"}, "I'm having surgery on my shoulder in May")),
    ("I'm getting a colonoscopy on Friday.",
     _item("surgeries", {"procedure": "Colonoscopy"}, "I'm getting a colonoscopy on Friday")),
])
def test_text_after_the_quote_is_read(msg, item):
    assert _one(msg, item) == [], msg


# H3: doubt and negative results are not diagnoses.
@pytest.mark.parametrize("msg,item", [
    ("My doctor thinks I have Lyme disease.",
     _item("diagnoses", {"diagnosis": "Lyme disease"}, "I have Lyme disease")),
    ("My doctor suspects I have Hashimoto's.",
     _item("diagnoses", {"diagnosis": "Hashimoto's"}, "I have Hashimoto's")),
    ("I'm worried I have cancer.", _item("diagnoses", {"diagnosis": "Cancer"}, "I have cancer")),
    ("I'm afraid I have diabetes.", _item("diagnoses", {"diagnosis": "Diabetes"}, "I have diabetes")),
    ("I'm at risk for osteoporosis.",
     _item("diagnoses", {"diagnosis": "Osteoporosis"}, "I'm at risk for osteoporosis")),
    ("I tested negative for Lyme.", _item("diagnoses", {"diagnosis": "Lyme"}, "I tested negative for Lyme")),
    ("My doctor ruled out MS.", _item("diagnoses", {"diagnosis": "MS"}, "My doctor ruled out MS")),
    ("I was tested for celiac and it was negative.",
     _item("diagnoses", {"diagnosis": "Celiac"}, "I was tested for celiac")),
    ("I was screened for breast cancer.",
     _item("diagnoses", {"diagnosis": "Breast cancer"}, "I was screened for breast cancer")),
    ("My surgeon wants to do a knee replacement.",
     _item("surgeries", {"procedure": "Knee replacement"}, "My surgeon wants to do a knee replacement")),
    ("I want a hip replacement.", _item("surgeries", {"procedure": "Hip replacement"}, "I want a hip replacement")),
    ("I'm on the waitlist for a knee replacement.",
     _item("surgeries", {"procedure": "Knee replacement"}, "I'm on the waitlist for a knee replacement")),
])
def test_doubt_and_negative_results_record_nothing(msg, item):
    assert _one(msg, item) == [], msg


def test_tested_positive_still_records():
    out = _one("I tested positive for Lyme.",
               _item("diagnoses", {"diagnosis": "Lyme"}, "I tested positive for Lyme"))
    assert out and out[0]["value"]["diagnosis"] == "Lyme"


# M1: a wished-for or past pregnancy.
@pytest.mark.parametrize("msg,quote", [
    ("I want to get pregnant.", "I want to get pregnant"),
    ("I'd like to get pregnant soon.", "I'd like to get pregnant soon"),
    ("I've been pregnant three times.", "I've been pregnant three times"),
])
def test_wished_or_past_pregnancy_records_nothing(msg, quote):
    assert _one(msg, _item("diagnoses", {"diagnosis": "Pregnancy"}, quote)) == [], msg


def test_pregnant_for_twenty_weeks_is_current():
    out = _one("I have been pregnant for 20 weeks.",
               _item("diagnoses", {"diagnosis": "Pregnancy"}, "I have been pregnant for 20 weeks"))
    assert out and out[0]["value"]["current"] == "Current"


# M2: tense in both directions.
@pytest.mark.parametrize("msg,diag,status", [
    ("I've had shingles twice.", "Shingles", "Past"),
    ("I've had pneumonia three times.", "Pneumonia", "Past"),
    ("I developed diabetes in 2015.", "Diabetes", "Current"),
    ("I was born with a heart murmur.", "Heart murmur", "Current"),
    ("I got Lyme in 2015 and still have symptoms.", "Lyme", "Current"),
    ("I've had asthma for years.", "Asthma", "Current"),
    ("I've been diagnosed with lupus.", "Lupus", "Current"),
])
def test_tense_round3(msg, diag, status):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, msg.rstrip(".")))
    assert out and out[0]["value"]["current"] == status, msg


@pytest.mark.parametrize("msg,med", [
    ("My thyroid was removed in 2010 so I take levothyroxine.", "Levothyroxine"),
    ("I had a heart attack in 2019 so I take a statin.", "Statin"),
])
def test_a_past_event_before_so_does_not_end_a_current_drug(msg, med):
    out = _one(msg, _item("medications", {"medication": med}, msg.rstrip(".")))
    assert out and out[0]["value"]["medication"] == med, msg


# M3: allergies that ended.
@pytest.mark.parametrize("msg,sens", [
    ("I outgrew my peanut allergy.", "Peanut"),
    ("I used to be allergic to cats as a kid.", "Cats"),
])
def test_an_ended_allergy_records_nothing(msg, sens):
    assert _one(msg, _item("allergies", {"sensitivity": sens}, msg.rstrip("."))) == [], msg


def test_a_past_reaction_is_still_an_allergy():
    out = _one("I had a bad reaction to penicillin once.",
               _item("allergies", {"sensitivity": "Penicillin"}, "I had a bad reaction to penicillin once"))
    assert len(out) == 1


# M4: a different condition sharing a prefix.
@pytest.mark.parametrize("msg,diag", [
    ("I have osteopenia.", "Osteoporosis"),
    ("I have diverticulosis.", "Diverticulitis"),
    ("I have endometrial thickening.", "Endometriosis"),
    ("I had hypothermia once hiking.", "Hypothyroidism"),
])
def test_a_shared_prefix_is_not_the_same_condition(msg, diag):
    assert _one(msg, _item("diagnoses", {"diagnosis": diag}, msg.rstrip("."))) == [], msg


@pytest.mark.parametrize("msg,diag", [
    ("I'm anemic.", "Anemia"), ("I'm diabetic.", "Diabetes"), ("I'm hypothyroid.", "Hypothyroidism"),
    ("I'm asthmatic.", "Asthma"), ("I'm epileptic.", "Epilepsy"),
])
def test_an_adjective_supports_its_condition(msg, diag):
    out = _one(msg, _item("diagnoses", {"diagnosis": diag}, msg.rstrip(".")))
    assert out and out[0]["value"]["diagnosis"] == diag, msg


def test_pacemaker_implant_from_i_have_a_pacemaker():
    out = _one("I have a pacemaker.", _item("surgeries", {"procedure": "Pacemaker implant"},
                                              "I have a pacemaker"))
    assert out and out[0]["value"]["procedure"] == "Pacemaker implant"


# M5: free text gets doubt, plan and (for trauma and toxins) negation checks.
@pytest.mark.parametrize("msg,field,quote", [
    ("I might have a cavity.", "dental", "I might have a cavity"),
    ("I think I have a cavity.", "dental", "I have a cavity"),
    ("My dentist wants to do a root canal.", "dental", "My dentist wants to do a root canal"),
    ("I'm thinking of getting my amalgams out.", "dental", "I'm thinking of getting my amalgams out"),
    ("I'm getting a root canal on Friday.", "dental", "I'm getting a root canal on Friday"),
    ("I'm going to try the keto diet.", "diet", "I'm going to try the keto diet"),
    ("I have never broken a bone.", "physical_trauma", "I have never broken a bone"),
    ("I don't think I've ever been exposed to mold.", "toxins", "I've ever been exposed to mold"),
    ("He drinks every night but I don't.", "alcohol", "He drinks every night but I don't"),
])
def test_free_text_doubt_plans_and_negation(msg, field, quote):
    assert _one(msg, _item(field, "x", quote)) == [], msg


@pytest.mark.parametrize("msg,field", [
    ("I don't drink alcohol at all.", "alcohol"),
    ("I don't eat gluten or dairy.", "diet"),
    ("I wake and need to pee twice a night.", "sleep"),
])
def test_free_text_negatives_and_needs_that_are_real_answers(msg, field):
    out = _one(msg, _item(field, "x", msg.rstrip(".")))
    assert out, msg


# L1: real facts that were dropped.
@pytest.mark.parametrize("msg,relative", [
    ("My brother and sister both have celiac.", "Sister"),
    ("My brother and sister both have celiac.", "Brother"),
    ("My mom and dad both had cancer.", "Father"),
    ("My mom and dad both had cancer.", "Mother"),
])
def test_joined_relatives_both_record(msg, relative):
    cond = "Celiac" if "celiac" in msg else "Cancer"
    out = _one(msg, _item("family_history", {"relative": relative, "condition": cond},
                          msg.rstrip("."), about="family"))
    assert out and out[0]["value"]["relative"] == relative, (msg, relative)


def test_a_relative_telling_the_client_about_family_records():
    out = _one("My mom told me my grandmother had diabetes.",
               _item("family_history", {"relative": "Grandmother", "condition": "Diabetes"},
                     "my grandmother had diabetes", about="family"))
    assert out and out[0]["value"]["relative"] == "Grandmother"


@pytest.mark.parametrize("msg,field,item_value,quote", [
    ("I take a baby aspirin every day.", "otc_drugs", {"medication": "Aspirin"},
     "I take a baby aspirin every day"),
    ("I take a children's chewable vitamin.", "supplements", {"name": "Chewable vitamin"},
     "I take a children's chewable vitamin"),
    ("I've been sleeping badly since my mom died.", "sleep", "x",
     "I've been sleeping badly since my mom died"),
])
def test_own_facts_naming_a_child_word_or_a_loss(msg, field, item_value, quote):
    assert _one(msg, _item(field, item_value, quote)), msg


def test_my_childrens_vitamin_is_still_theirs():
    assert _one("My children's vitamin has iron.",
                _item("supplements", {"name": "Vitamin"}, "My children's vitamin has iron")) == []


# L2: a tag question.
@pytest.mark.parametrize("msg", ["I have candida, right?", "I have candida, is that right?"])
def test_a_tag_question_records_nothing(msg):
    assert _one(msg, _item("diagnoses", {"diagnosis": "Candida"}, "I have candida")) == [], msg


# L3: the apply path.
def test_a_list_in_a_text_field_is_joined_not_reprd():
    assert hs.apply_suggestion("diet", ["vegan"], "keto") == "vegan\nketo"


def test_a_row_already_held_with_more_columns_adds_nothing():
    have = [{"medication": "Metformin", "reason": "diabetes"}]
    assert hs.apply_suggestion("medications", have, {"medication": "Metformin"}) == have


def test_a_row_with_new_detail_is_still_appended():
    have = [{"medication": "Metformin"}]
    assert hs.apply_suggestion("medications", have, {"medication": "Metformin",
                                                     "reason": "diabetes"}) == [
        {"medication": "Metformin"}, {"medication": "Metformin", "reason": "diabetes"}]


# Round 3 surviving mutations.
def test_unless_is_hypothetical():
    assert _one("Unless I have diabetes, I'm fine.",
                _item("diagnoses", {"diagnosis": "Diabetes"}, "I have diabetes")) == []


def test_age_written_as_yo():
    out = _one("I had my tonsils out as a 9 yo.",
               _item("surgeries", {"procedure": "Tonsils removal", "age": 9}, "I had my tonsils out"))
    assert out and out[0]["value"].get("age") == 9


def test_a_three_letter_word_must_be_said_exactly():
    assert _one("I have IBSO.", _item("diagnoses", {"diagnosis": "IBS"}, "I have IBSO")) == []
    assert _one("I have IBS.", _item("diagnoses", {"diagnosis": "IBS"}, "I have IBS"))


def test_a_fenced_reply_without_its_closing_fence_is_garbage():
    reply = '```json\n{"items": [{"field_id": "medications", "value": {"medication": "Warfarin"}, ' \
            '"quote": "I take warfarin", "about": "self"}]}'
    assert hs.extract_from_turn("I take warfarin.", "",
                                extractor=hs.llm_extractor(lambda s, u: reply)) == []


@pytest.mark.parametrize("msg,item", [
    ("I won't take metformin.", _item("medications", {"medication": "Metformin"}, "I won't take metformin")),
    ("I wouldn't have a hip replacement.",
     _item("surgeries", {"procedure": "Hip replacement"}, "I wouldn't have a hip replacement")),
])
def test_wont_and_wouldnt_are_negation(msg, item):
    assert _one(msg, item) == [], msg


@pytest.mark.parametrize("msg,quote,ok", [
    ("I have diabetes also asthma.", "asthma", True),
    ("The cleanse helped diabetes and asthma.", "asthma", False),
    ("I take metformin but the doctor says gout and asthma.", "asthma", False),
])
def test_conjoined_fact_edges(msg, quote, ok):
    assert bool(_one(msg, _item("diagnoses", {"diagnosis": "Asthma"}, quote))) is ok, msg


def test_a_date_to_come_after_the_quote_records_nothing():
    assert _one("I have my gallbladder surgery next month.",
                _item("surgeries", {"procedure": "Gallbladder surgery"},
                      "I have my gallbladder surgery")) == []


def test_a_four_letter_word_needs_four_shared_letters():
    assert _one("I have lymph node swelling.",
                _item("diagnoses", {"diagnosis": "Lyme"}, "I have lymph node swelling")) == []


def test_a_fenced_reply_with_trailing_junk_is_garbage():
    reply = '```json\n{"items": [{"field_id": "medications", "value": {"medication": "Warfarin"}, ' \
            '"quote": "I take warfarin", "about": "self"}]}abc'
    assert hs.extract_from_turn("I take warfarin.", "",
                                extractor=hs.llm_extractor(lambda s, u: reply)) == []


def test_tomorrow_after_the_quote_records_nothing():
    assert _one("I have my knee surgery tomorrow.",
                _item("surgeries", {"procedure": "Knee surgery"}, "I have my knee surgery")) == []
