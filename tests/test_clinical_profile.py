from dashboard.clinical_profile import consolidate


def test_consolidates_intake_and_portal_health_history_for_mining():
    profile = consolidate(
        {"email": "j@x.com", "conditions": ["Eczema"], "challenges": "Fatigue"},
        {"status": "submitted", "answers": {
            "health_concerns": [{"concern": "Migraines", "rating": 8}],
            "diagnoses": [{"diagnosis": "Hashimoto's", "current": "Current"}],
            "medications": [{"medication": "Levothyroxine", "reason": "Thyroid"}],
            "obstacles": "Poor sleep",
        }},
        {"supplements_text": "Magnesium"},
        {"answers": {"toxins_text": "Mold exposure", "toxins_yes": True}},
    )
    assert profile["conditions"] == ["Eczema", "Migraines", "Hashimoto's — Current"]
    assert profile["intake_submitted"] is True
    assert all(x in profile["challenges"] for x in
               ("Fatigue", "Poor sleep", "Levothyroxine", "Magnesium", "Mold exposure"))
