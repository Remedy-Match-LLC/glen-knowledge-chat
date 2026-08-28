import sqlite3

from dashboard.biofield_authoring import add_chain_row, create_test
from dashboard.biofield_clinical_checklist import balance_item, build, profile_labels
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
        {"label": "Chronic migraine", "checked": True, "covered_by": "Neuroprotect",
         "layer": None, "common_remedies": []},
        {"label": "Fatigue", "checked": False, "covered_by": "", "layer": None,
         "common_remedies": []},
    ]


def test_historical_related_remedy_checks_when_added_to_program():
    rows = build(
        {"conditions": "Eczema"},
        [{"head": "Liver", "remedy": "Skin Restore"}],
        remedy_lookup=lambda label: [{"remedy": "Skin Restore", "count": 4}],
    )
    assert rows[0]["checked"] is True
    assert rows[0]["covered_by"] == "Skin Restore"
    assert rows[0]["common_remedies"] == ["Skin Restore"]


def test_existing_stress_coverage_checks_condition():
    rows = build(
        {"conditions": ["Sleep disturbance"]}, [],
        {"balanced": [{"label": "Sleep", "balanced_by": "Sleep Ease"}]},
    )
    assert rows[0]["checked"] is True


def test_checklist_renders_directly_before_causal_chain():
    report = {"test_id": "a1", "client": {"name": "Pam", "email": "p@x.com"},
              "layers": [
                  {"layer": 1, "head": "Liver support", "remedy": "Liver Support"},
                  {"layer": 2, "head": "Neurological support", "remedy": "Neuroprotect"},
              ]}
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
    assert "initClinicalDrag" in html
    assert "clinical-items/order" in html
    assert "Drag to reorder" in html
    assert "Add to layer" in html
    assert "Add remedy" in html
    assert "Choose layer" in html
    assert "Layer 1: Liver support" in html
    assert "Layer 2: Neurological support" in html
    assert "New layer 3" in html


def test_checklist_selects_current_layer_and_offers_first_new_layer():
    html = render_author_html(
        {"test_id": "a1", "client": {},
         "layers": [{"layer": 1, "stored_layer": 4, "head": "Immune support",
                     "remedy": "Terrain Restore"}]},
        clinical_checklist=[
            {"label": "Fatigue", "checked": True, "covered_by": "Terrain Restore",
             "layer": 4, "common_remedies": []},
        ],
    )
    assert "value='4' selected" in html
    assert "Layer 1: Immune support" in html
    assert "value='5'>New layer 2" in html


def test_balance_item_adds_tail_head_and_multiple_remedies():
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Pam", "p@x.com", "2026-08-27")
    add_chain_row(cx, tid, 2, "", "Brain", "Existing")
    result = balance_item(cx, tid, "Chronic migraine", 2,
                          ["Neuroprotect", "Magnesium"])
    rows = cx.execute(
        "SELECT head,most_affected,remedy FROM biofield_auth_chain WHERE layer=2 ORDER BY id"
    ).fetchall()
    assert result["added_remedies"] == ["Neuroprotect", "Magnesium"]
    assert rows[0] == ("Chronic migraine", "Brain, Chronic migraine", "Existing")
    assert [row[2] for row in rows] == ["Existing", "Neuroprotect", "Magnesium"]


def test_balance_item_preserves_existing_head_and_deduplicates_tail_and_remedy():
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Pam", "p@x.com", "2026-08-27")
    add_chain_row(cx, tid, 3, "Inflammation", "Fatigue", "Sustain")
    balance_item(cx, tid, "Fatigue", 3, ["Sustain"])
    assert cx.execute(
        "SELECT head,most_affected,remedy FROM biofield_auth_chain WHERE layer=3"
    ).fetchall() == [("Inflammation", "Fatigue", "Sustain")]
