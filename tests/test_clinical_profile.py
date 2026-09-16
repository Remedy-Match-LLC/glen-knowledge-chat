from dashboard.clinical_profile import consolidate

def test_consolidates_all_client_entered_history():
    p = consolidate({"conditions": ["Eczema"], "challenges": "Fatigue"},
        {"status": "submitted", "answers": {"health_concerns": [{"concern": "Migraines"}], "diagnoses": [{"diagnosis": "Hashimoto's", "current": "Current"}], "medications": [{"medication": "Levothyroxine", "reason": "Thyroid"}], "obstacles": "Poor sleep"}},
        {"supplements_text": "Magnesium"}, {"answers": {"toxins_text": "Mold exposure"}})
    assert p["conditions"] == ["Eczema", "Migraines", "Hashimoto's — Current"]
    assert p["intake_submitted"] is True
    assert all(x in p["challenges"] for x in ("Fatigue", "Poor sleep", "Levothyroxine", "Magnesium", "Mold exposure"))


def test_historical_snapshots_feed_summary_without_replacing_current_data():
    p = consolidate(
        {"conditions": ["Dry eye"], "challenges": "Current concern", "goals": "Current goal"},
        {"status": "submitted", "answers": {
            "health_concerns": [{"concern": "Dry eye"}],
            "other_symptoms": "Current light sensitivity",
        }},
        historical_snapshots=[{
            "id": 284, "form_date": "2024-11-19",
            "form_name": "FileMaker Contacts: Application",
            "review_status": "staff_review",
            "answers": {
                "health_concerns": [{"concern": "High IOP"}],
                "other_symptoms": "Photophobia",
                "surgeries": [{"procedure": "Cataract surgery"}],
                "legacy_application_fields": {
                    "Wellness Goals": "Stay healthy", "Chronicity": "12+ yrs",
                },
            },
        }],
    )
    assert p["conditions"] == ["Dry eye", "High IOP"]
    assert "Current light sensitivity" in p["challenges"]
    assert "[Historical intake — 2024-11-19]" in p["challenges"]
    assert all(x in p["challenges"] for x in ("Photophobia", "Cataract surgery", "12+ yrs"))
    assert p["goals"] == "Current goal\nStay healthy"
    assert p["historical_intake_count"] == 1
    assert p["historical_intake_sources"][0]["review_status"] == "staff_review"


def test_json_string_conditions_column_is_parsed_not_comma_split():
    """people.conditions is written by canonical_tags as json.dumps(list). Splitting
    that string on commas shredded it into fragments like '["Adrenal Fatigue' and
    'Current', which then appeared as rows in the authoring Clinical summary."""
    p = consolidate({"conditions": '["Adrenal Fatigue, Current, 2023", "PCOS, 2012"]'})
    assert p["conditions"] == ["Adrenal Fatigue, Current, 2023", "PCOS, 2012"]


def test_plain_comma_string_conditions_still_split():
    p = consolidate({"conditions": "Dry eye, Migraine"})
    assert p["conditions"] == ["Dry eye", "Migraine"]


def test_intake_priorities_keep_form_order_with_rating_and_onset():
    """The intake asks for concerns 'in order of importance'. Form order is the
    client's own ranking. The 1-10 rating is NOT a sort key: of the three clients
    who filled it in, one used it as importance (three 10s) and one as a rank
    (eyes=1, her main concern). So carry rating through and never sort on it."""
    p = consolidate({}, {"status": "submitted", "answers": {"health_concerns": [
        {"concern": "eyes", "rating": 1, "years_since_onset": 2020},
        {"concern": "hormones", "rating": 2},
        {"concern": "weight", "rating": 3, "years_since_onset": 20},
    ]}})
    assert [x["concern"] for x in p["intake_priorities"]] == ["eyes", "hormones", "weight"]
    assert p["intake_priorities"][0] == {"concern": "eyes", "rating": 1,
                                         "years_since_onset": 2020}
    assert p["intake_priorities"][1]["years_since_onset"] is None


def test_intake_priorities_skip_blank_concerns():
    p = consolidate({}, {"status": "submitted", "answers": {"health_concerns": [
        {"concern": "  ", "rating": 9}, {"rating": 4}, {"concern": "Dry eye"},
    ]}})
    assert [x["concern"] for x in p["intake_priorities"]] == ["Dry eye"]


def test_intake_priorities_empty_without_intake():
    assert consolidate({"conditions": ["Dry eye"]})["intake_priorities"] == []
