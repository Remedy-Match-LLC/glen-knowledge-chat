from dashboard.biofield_clinical_checklist import build, profile_labels
from dashboard.biofield_report_html import render_author_html


def test_profile_labels_keeps_structured_clinical_items_only():
    profile = {
        "conditions": ["Migraine", "Fatigue"],
        "tags": ["pb:wet-amd", "topic-sales", "client"],
        "challenges": "Her son has seizures",
    }
    assert profile_labels(profile) == ["Migraine", "Fatigue", "Wet AMD"]


def test_layer_remedy_checks_related_condition():
    rows = build(
        {"conditions": ["Chronic migraine", "Fatigue"]},
        [{"head": "Migraine", "most_affected": "Brain", "remedy": "Neuroprotect"}],
    )
    assert rows == [
        {"label": "Chronic migraine", "checked": True, "covered_by": "Neuroprotect"},
        {"label": "Fatigue", "checked": False, "covered_by": ""},
    ]


def test_historical_related_remedy_checks_when_added_to_program():
    rows = build(
        {"conditions": "Eczema"},
        [{"head": "Liver", "remedy": "Skin Restore"}],
        remedy_lookup=lambda label: [{"remedy": "Skin Restore", "count": 4}],
    )
    assert rows[0]["checked"] is True
    assert rows[0]["covered_by"] == "Skin Restore"


def test_existing_stress_coverage_checks_condition():
    rows = build(
        {"conditions": ["Sleep disturbance"]}, [],
        {"balanced": [{"label": "Sleep", "balanced_by": "Sleep Ease"}]},
    )
    assert rows[0]["checked"] is True


def test_checklist_renders_directly_before_causal_chain():
    report = {"test_id": "a1", "client": {"name": "Pam", "email": "p@x.com"},
              "layers": []}
    html = render_author_html(
        report,
        clinical_checklist=[
            {"label": "Migraine", "checked": True, "covered_by": "Neuroprotect"},
            {"label": "Fatigue", "checked": False, "covered_by": ""},
        ],
    )
    assert html.index("Clinical summary") < html.index("Causal chain")
    assert "1 of 2 covered" in html
    assert "Neuroprotect" in html
    assert "Needs remedy coverage" in html
    assert "+ Add item" in html
    assert "removeClinicalItem" in html
