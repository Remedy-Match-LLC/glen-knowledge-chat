from dashboard.clinical_profile import consolidate

def test_consolidates_all_client_entered_history():
    p = consolidate({"conditions": ["Eczema"], "challenges": "Fatigue"},
        {"status": "submitted", "answers": {"health_concerns": [{"concern": "Migraines"}], "diagnoses": [{"diagnosis": "Hashimoto's", "current": "Current"}], "medications": [{"medication": "Levothyroxine", "reason": "Thyroid"}], "obstacles": "Poor sleep"}},
        {"supplements_text": "Magnesium"}, {"answers": {"toxins_text": "Mold exposure"}})
    assert p["conditions"] == ["Eczema", "Migraines", "Hashimoto's — Current"]
    assert p["intake_submitted"] is True
    assert all(x in p["challenges"] for x in ("Fatigue", "Poor sleep", "Levothyroxine", "Magnesium", "Mold exposure"))
