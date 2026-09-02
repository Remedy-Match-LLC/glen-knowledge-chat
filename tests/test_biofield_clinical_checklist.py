import sqlite3

from dashboard.biofield_authoring import add_chain_row, create_test
from dashboard.biofield_clinical_checklist import (
    add_related_remedy, balance_item, build, catalog_items, forget_remedy,
    profile_labels, program_remedies, remembered_remedies, related_remedies,
    remember_remedy,
)
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
         "covered_remedies": ["Neuroprotect"], "layer": None, "common_remedies": []},
        {"label": "Fatigue", "checked": False, "covered_by": "", "covered_remedies": [], "layer": None,
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


def test_glaucoma_uses_approved_condition_program_remedies():
    names = program_remedies("Glaucoma")
    assert "Neuroprotect" in names
    assert "OcuFlow Bedtime" in names
    assert "OcuFlow Daytime" in names


def test_manual_condition_remedy_relationship_persists_per_test():
    cx = sqlite3.connect(":memory:")
    assert add_related_remedy(cx, "a33", "Glaucoma", "Custom Eye Support")
    assert related_remedies(cx, "a33", "Glaucoma") == ["Custom Eye Support"]
    assert related_remedies(cx, "a34", "Glaucoma") == []


def test_practitioner_condition_remedy_memory_is_global_and_deletable():
    cx = sqlite3.connect(":memory:")
    assert remember_remedy(cx, "Histamine intolerance", "Aller Ease") == "Aller Ease"
    assert remembered_remedies(cx, "histamine intolerance") == ["Aller Ease"]
    assert catalog_items(cx, "histamine") == [
        {"label": "Histamine intolerance", "remedy_count": 1}
    ]
    assert forget_remedy(cx, "Histamine intolerance", "Aller Ease") == "Aller Ease"
    assert remembered_remedies(cx, "Histamine intolerance") == []


def test_condition_catalog_includes_amd_aliases_and_excludes_historical_noise():
    cx = sqlite3.connect(":memory:")
    cx.executescript("""
        CREATE TABLE fmp_snap_client_active_main_stress(id_pk INTEGER,main_stress TEXT);
        CREATE TABLE fmp_snap_client_causal_chain(id_pk INTEGER,id_fk_active_stress INTEGER);
        CREATE TABLE fmp_snap_client_remedy(id_fk_causal_chain INTEGER,remedy TEXT);
        INSERT INTO fmp_snap_client_active_main_stress VALUES(1,'Left Retina'),(2,'Neuroprotect');
        INSERT INTO fmp_snap_client_causal_chain VALUES(10,1),(20,2);
        INSERT INTO fmp_snap_client_remedy VALUES(10,'Macular Wellness Lutein'),(20,'Neuroprotect');
    """)
    labels = [row["label"] for row in catalog_items(cx, "amd")]
    assert "Dry AMD" in labels
    assert "Wet AMD" in labels
    assert "AMD (Age-Related Macular Degeneration)" in labels
    assert "Left Retina" not in labels
    assert "Neuroprotect" not in labels
    assert program_remedies("Dry Macular Degeneration")
    assert program_remedies("AMD (Age-Related Macular Degeneration)")


def test_existing_stress_coverage_checks_condition():
    rows = build(
        {"conditions": ["Sleep disturbance"]}, [],
        {"balanced": [{"label": "Sleep", "balanced_by": "Sleep Ease"}]},
    )
    assert rows[0]["checked"] is True


def test_multiple_related_remedies_on_one_layer_all_persist_as_covered():
    rows = build(
        {"conditions": ["Glaucoma"]},
        [
            {"stored_layer": 1, "head": "Glaucoma", "remedy": "OcuFlow Bedtime"},
            {"stored_layer": 1, "head": "", "most_affected": "", "remedy": "IOP Syntropy"},
        ],
        remedy_lookup=lambda label: ["OcuFlow Bedtime", "IOP Syntropy"],
    )
    assert rows[0]["covered_remedies"] == ["OcuFlow Bedtime", "IOP Syntropy"]
    assert rows[0]["covered_by"] == "OcuFlow Bedtime, IOP Syntropy"


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
    assert "Add to remedy list" in html
    assert "clinical-items/remedies" in html
    assert "type=checkbox aria-label=\"Select Fatigue\"" in html
    assert "onchange=toggleClinicalItem(this)" in html
    assert "Assign to layer" in html
    assert "Choose layer" in html
    assert "Layer 1: Liver support" in html
    assert "Layer 2: Neurological support" in html
    assert "New layer 3" in html
    assert "list=clinicalCatalog" in html
    assert "loadClinicalCatalog()" in html
    assert "deleteClinicalRemedy" in html


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
