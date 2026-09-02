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
