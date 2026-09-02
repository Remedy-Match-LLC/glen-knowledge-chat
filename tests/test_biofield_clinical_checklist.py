import sqlite3

from dashboard.biofield_authoring import add_chain_row, create_test
from dashboard.biofield_clinical_checklist import (
    balance_item, build, catalog_items, custom_remedies, ensure_catalog_schema,
    forget_remedy,
    profile_labels, program_remedies, remember_remedies, remember_stress_pattern,
    stress_pattern, suggested_pattern,
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
         "layer": None, "common_remedies": [], "stress_pattern": "",
         "remembered_pattern": "", "pattern_is_suggested": False},
        {"label": "Fatigue", "checked": False, "covered_by": "", "layer": None,
         "common_remedies": [], "stress_pattern": "", "remembered_pattern": "",
         "pattern_is_suggested": False},
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
    assert "type=checkbox aria-label=\"Select Fatigue\"" in html
    assert "onchange=toggleClinicalItem(this)" in html
    assert "Assign to layer" in html
    assert "Choose layer" in html
    assert "Layer 1: Liver support" in html
    assert "Layer 2: Neurological support" in html
    assert "New layer 3" in html
    assert "list=clinicalCatalog" in html
    assert "loadClinicalCatalog()" in html
    assert "+ Remedy" in html
    assert "deleteClinicalRemedy" in html


def test_custom_condition_remedies_are_remembered_searchable_and_deletable():
    cx = sqlite3.connect(":memory:")
    assert remember_remedies(cx, "Histamine intolerance", ["Aller Ease", "Liver Support"]) == 2
    assert custom_remedies(cx, "histamine intolerance") == ["Aller Ease", "Liver Support"]
    assert catalog_items(cx, "histamine") == [
        {"label": "Histamine intolerance", "remedy_count": 2}
    ]
    assert forget_remedy(cx, "Histamine intolerance", "Aller Ease") is True
    assert custom_remedies(cx, "Histamine intolerance") == ["Liver Support"]


def test_catalog_has_amd_aliases_without_historical_remedies_or_locations():
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
    assert "Dry AMD" in labels and "Wet AMD" in labels
    assert "AMD (Age-Related Macular Degeneration)" in labels
    assert "Left Retina" not in labels and "Neuroprotect" not in labels
    assert program_remedies("Dry Macular Degeneration")


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
    assert custom_remedies(cx, "Chronic migraine") == ["Magnesium", "Neuroprotect"]


def test_balance_item_preserves_existing_head_and_deduplicates_tail_and_remedy():
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Pam", "p@x.com", "2026-08-27")
    add_chain_row(cx, tid, 3, "Inflammation", "Fatigue", "Sustain")
    balance_item(cx, tid, "Fatigue", 3, ["Sustain"])
    assert cx.execute(
        "SELECT head,most_affected,remedy FROM biofield_auth_chain WHERE layer=3"
    ).fetchall() == [("Inflammation", "Fatigue", "Sustain")]


def test_stress_pattern_is_remembered_per_condition_and_replaced_only_on_request():
    with sqlite3.connect(":memory:") as cx:
        assert remember_stress_pattern(cx, "Chronic migraine", "Cerebral vascular spasm")
        assert stress_pattern(cx, "chronic  MIGRAINE") == "Cerebral vascular spasm"
        # A second term never quietly overwrites the remembered one.
        assert remember_stress_pattern(cx, "Chronic migraine", "Cranial nerve irritation") is False
        assert stress_pattern(cx, "Chronic migraine") == "Cerebral vascular spasm"
        assert remember_stress_pattern(cx, "Chronic migraine", "Cranial nerve irritation",
                                       replace=True)
        assert stress_pattern(cx, "Chronic migraine") == "Cranial nerve irritation"


def test_build_carries_the_remembered_stress_pattern():
    rows = build({"conditions": ["Fatigue"]}, [],
                 stress_lookup=lambda label: "Adrenal exhaustion")
    assert rows[0]["stress_pattern"] == "Adrenal exhaustion"
    assert rows[0]["remembered_pattern"] == "Adrenal exhaustion"


def test_balance_writes_the_stress_pattern_as_head_and_tail():
    with sqlite3.connect(":memory:") as cx:
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        balance_item(cx, tid, "Chronic migraine", 1, ["Neuroprotect"],
                     pattern="Cerebral vascular spasm")
        rows = cx.execute(
            "SELECT head,most_affected,remedy FROM biofield_auth_chain WHERE layer=1 ORDER BY id"
        ).fetchall()
        assert rows[0][0] == "Cerebral vascular spasm"
        assert rows[0][1] == "Cerebral vascular spasm"
        assert [r[2] for r in rows if r[2]] == ["Neuroprotect"]
        # Entering a pattern for a condition with none remembered records it for next time.
        assert stress_pattern(cx, "Chronic migraine") == "Cerebral vascular spasm"


def test_balance_without_a_pattern_still_uses_the_item_label():
    with sqlite3.connect(":memory:") as cx:
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        balance_item(cx, tid, "Fatigue", 1, [])
        head, tail = cx.execute(
            "SELECT head,most_affected FROM biofield_auth_chain WHERE layer=1").fetchone()
        assert head == "Fatigue" and tail == "Fatigue"
        assert stress_pattern(cx, "Fatigue") == ""


def test_adding_to_an_existing_layer_only_appends_the_pattern_to_the_tail():
    with sqlite3.connect(":memory:") as cx:
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        add_chain_row(cx, tid, 1, "Gut dysbiosis", "Gut lining", "Terrain Restore",
                      confirmed=1, origin="live")
        balance_item(cx, tid, "Chronic migraine", 1, ["Neuroprotect"],
                     pattern="Cerebral vascular spasm")
        rows = cx.execute(
            "SELECT head,most_affected,remedy FROM biofield_auth_chain WHERE layer=1 ORDER BY id"
        ).fetchall()
        # The layer keeps its own head; the pattern joins the tail listing.
        assert rows[0][0] == "Gut dysbiosis"
        assert rows[0][1] == "Gut lining, Cerebral vascular spasm"
        assert [r[2] for r in rows if r[2]] == ["Terrain Restore", "Neuroprotect"]
        # The remedy row repeats its layer's head, so the card keeps it instead of
        # splitting it off into a layer of its own.
        assert rows[1][0] == "Gut dysbiosis"


def test_older_local_databases_named_the_column_item_label():
    """Local DBs built before the rename carry `item_label`; every reader and writer
    here speaks `label`, so the table was unwritable until it is migrated."""
    with sqlite3.connect(":memory:") as cx:
        cx.execute("""CREATE TABLE biofield_clinical_catalog(
            item_key TEXT NOT NULL, item_label TEXT NOT NULL,
            remedy_key TEXT NOT NULL, remedy TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(item_key,remedy_key))""")
        cx.execute("INSERT INTO biofield_clinical_catalog VALUES"
                   "('herpes','Herpes','virex','VirEx',0,CURRENT_TIMESTAMP)")
        ensure_catalog_schema(cx)
        # The rows already recorded survive the rename.
        assert custom_remedies(cx, "Herpes") == ["VirEx"]
        # And the table takes writes again.
        assert remember_remedies(cx, "Dry Eye", ["Moisturize"]) == 1
        assert custom_remedies(cx, "Dry Eye") == ["Moisturize"]


def test_every_catalogue_condition_has_a_suggested_functional_term():
    """The picker offers these labels as clinical items, so each needs a term --
    except 'Often no symptoms early', which names no function to restore."""
    import json, os
    seed = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                       "data", "condition_programs_seed.json")))
    wanted = set()
    for program in seed["condition_programs"].values():
        if program.get("label"):
            wanted.add(program["label"])
        wanted.update(program.get("symptoms") or [])
    wanted.discard("Often no symptoms early")
    missing = sorted(label for label in wanted if not suggested_pattern(label))
    assert missing == []
    assert suggested_pattern("Often no symptoms early") == ""


def test_suggested_patterns_match_the_practitioners_own_wording():
    assert suggested_pattern("Dry Eye") == "Tear Quality"
    assert suggested_pattern("Glaucoma — Elevated IOP") == "Eye Pressure Regulation"
    # The two glaucomas are clinically distinct and must not share a term.
    assert suggested_pattern("Glaucoma — Normal / Low IOP") == "Optic Nerve Perfusion"


def test_a_free_text_condition_gets_no_suggestion():
    assert suggested_pattern("Glaucoma & upper back pain from work") == ""
    assert suggested_pattern("") == ""


def test_build_marks_a_suggestion_and_lets_a_remembered_term_win():
    rows = build({"conditions": ["Sleep Difficulty"]}, [])
    assert rows[0]["stress_pattern"] == "Sleep Regulation"
    assert rows[0]["pattern_is_suggested"] is True
    assert rows[0]["remembered_pattern"] == ""      # nothing to replace yet

    rows = build({"conditions": ["Sleep Difficulty"]}, [],
                 stress_lookup=lambda label: "Circadian Entrainment")
    assert rows[0]["stress_pattern"] == "Circadian Entrainment"
    assert rows[0]["pattern_is_suggested"] is False
    assert rows[0]["remembered_pattern"] == "Circadian Entrainment"


def test_using_a_suggestion_promotes_it_to_the_practitioners_own_term():
    with sqlite3.connect(":memory:") as cx:
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        assert stress_pattern(cx, "Sleep Difficulty") == ""
        balance_item(cx, tid, "Sleep Difficulty", 1, ["Sleep Ease"],
                     pattern=suggested_pattern("Sleep Difficulty"))
        assert stress_pattern(cx, "Sleep Difficulty") == "Sleep Regulation"
        head, tail = cx.execute(
            "SELECT head,most_affected FROM biofield_auth_chain WHERE layer=1").fetchone()
        assert head == "Sleep Regulation" and tail == "Sleep Regulation"
