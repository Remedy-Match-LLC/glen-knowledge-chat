import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import (
    add_chain_row, create_test, init_auth_tables, ordered_chain,
)
from dashboard.biofield_report_html import group_layers
from dashboard.biofield_clinical_proposals import (
    accepted_labels, apply_order, apply_selection, decide, decisions, dismissed_labels,
    proposals, save_order, save_pattern, save_selection,
)
from dashboard.biofield_clinical_checklist import (
    remember_remedies, remember_stress_pattern, stress_pattern,
)


def test_proposals_exclude_existing_and_previously_decided_items():
    context = {"recent_feedback": [{
        "summary": "Pam wrote that migraines returned; her son has seizures.",
        "received_at": "2026-08-20",
    }]}
    rows = proposals(
        context, ["Migraine", "Seizures"], ["Migraine"],
        {"seizures": {"status": "dismissed"}},
    )
    assert rows == []


def test_decisions_persist_acceptance_and_dismissal(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        assert decide(cx, "a1", "Fatigue", "accepted", "I am exhausted")
        assert decide(cx, "a1", "Son's seizures", "dismissed", "My son...")
        assert accepted_labels(cx, "a1") == ["Fatigue"]
        assert dismissed_labels(cx, "a1") == ["Son's seizures"]
        assert decisions(cx, "a1")["son s seizures"]["status"] == "dismissed"


def test_routes_propose_then_accept_without_auto_adding(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-08-26")
    comms = {"recent_feedback": [{
        "summary": "Her son Adam has seizures.", "conditions": ["Seizures"],
        "received_at": "2026-08-24",
    }]}
    app = create_app(
        db,
        complete=lambda system, user: json.dumps({"stresses": ["Seizures"]}),
        fetch_recent_comms=lambda email: comms,
        fetch_profile=lambda email: {"conditions": ["Fatigue"]},
    )
    client = app.test_client()

    proposed = client.get(f"/author/{tid}/clinical-proposals").get_json()["items"]
    assert [x["label"] for x in proposed] == ["Seizures"]
    with sqlite3.connect(db) as cx:
        assert accepted_labels(cx, tid) == []

    response = client.post(f"/author/{tid}/clinical-proposals", json={
        "label": "Seizures", "evidence": "Her son Adam has seizures.", "status": "accepted",
    })
    assert response.get_json()["ok"] is True
    with sqlite3.connect(db) as cx:
        assert accepted_labels(cx, tid) == ["Seizures"]
    assert client.get(f"/author/{tid}/clinical-proposals").get_json()["items"] == []


def test_manual_checklist_add_and_remove_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-08-26")
    client = create_app(db, fetch_profile=lambda email: {"conditions": ["Fatigue"]}).test_client()

    assert client.post(f"/author/{tid}/clinical-items", json={
        "action": "add", "label": "Dry eyes",
    }).get_json()["ok"]
    assert b"Dry eyes" in client.get(f"/author/{tid}").data

    assert client.post(f"/author/{tid}/clinical-items", json={
        "action": "remove", "label": "Fatigue",
    }).get_json()["ok"]
    page = client.get(f"/author/{tid}").data
    assert b"Fatigue" not in page
    assert b"Dry eyes" in page


def test_checklist_order_persists_and_new_items_append(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        assert save_order(cx, "a1", ["Fatigue", "Migraine"]) == 2
        items = [{"label": "Migraine"}, {"label": "New symptom"}, {"label": "Fatigue"}]
        assert [x["label"] for x in apply_order(cx, "a1", items)] == [
            "Fatigue", "Migraine", "New symptom",
        ]


def test_checklist_order_route_restores_sequence(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-08-26")
    profile = {"conditions": ["Fatigue", "Migraine", "Dry eyes"]}
    client = create_app(db, fetch_profile=lambda email: profile).test_client()

    response = client.post(f"/author/{tid}/clinical-items/order", json={
        "labels": ["Dry eyes", "Migraine", "Fatigue"],
    })
    assert response.get_json() == {"ok": True, "count": 3}
    page = client.get(f"/author/{tid}").data.decode()
    assert page.index('data-label="Dry eyes"') < page.index('data-label="Migraine"')
    assert page.index('data-label="Migraine"') < page.index('data-label="Fatigue"')


def test_remedy_ticks_persist_per_test_and_survive_a_rerender(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        assert save_selection(cx, "a1", "Fatigue", ["Adrenal Restore", "Mitochondrial"]) == 2
        items = [{"label": "Fatigue", "covered_by": "", "common_remedies": ["Adrenal Restore"]},
                 {"label": "Migraine", "covered_by": "Neuroprotect",
                  "common_remedies": ["Neuroprotect"]}]
        rows = apply_selection(cx, "a1", items)
        assert rows[0]["selection_saved"] is True
        assert rows[0]["selected_remedies"] == ["Adrenal Restore", "Mitochondrial"]
        # A tick must never be orphaned by the common-remedy cap or a forgotten remedy.
        assert rows[0]["common_remedies"] == ["Adrenal Restore", "Mitochondrial"]
        # An item the practitioner never touched keeps deriving from the chain.
        assert "selection_saved" not in rows[1]


def test_clearing_every_tick_is_remembered_as_empty(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        save_selection(cx, "a1", "Migraine", ["Neuroprotect"])
        assert save_selection(cx, "a1", "Migraine", []) == 0
        rows = apply_selection(cx, "a1", [{"label": "Migraine", "covered_by": "Neuroprotect",
                                           "common_remedies": ["Neuroprotect"]}])
        assert rows[0]["selection_saved"] is True
        assert rows[0]["selected_remedies"] == []


def test_selection_route_survives_the_page_reload_other_actions_trigger(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-08-26")
        remember_remedies(cx, "Fatigue", ["Adrenal Restore", "Mitochondrial"])
    profile = {"conditions": ["Fatigue"]}
    client = create_app(db, fetch_profile=lambda email: profile).test_client()

    response = client.post(f"/author/{tid}/clinical-items/selection", json={
        "label": "Fatigue", "remedies": ["Mitochondrial"],
    })
    assert response.get_json() == {"ok": True, "count": 1}
    # Import Reveal and friends all end in location.reload(); the tick must come back.
    page = client.get(f"/author/{tid}").data.decode()
    assert ('value="Mitochondrial" checked onchange=selectClinicalRemedy(this)>') in page
    assert ('value="Adrenal Restore" onchange=selectClinicalRemedy(this)>') in page


def test_selection_route_rejects_a_missing_remedies_list(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-08-26")
    client = create_app(db, fetch_profile=lambda email: {}).test_client()
    assert client.post(f"/author/{tid}/clinical-items/selection",
                       json={"label": "Fatigue"}).status_code == 400


def test_a_typed_stress_pattern_survives_the_reload_and_can_replace_the_remembered_one(
        tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        remember_stress_pattern(cx, "Fatigue", "Adrenal exhaustion")
    client = create_app(db, fetch_profile=lambda email: {"conditions": ["Fatigue"]}).test_client()

    page = client.get(f"/author/{tid}").data.decode()
    assert 'class=clinical-stress list=vocab value="Adrenal exhaustion"' in page
    assert 'data-remembered="Adrenal exhaustion"' in page

    # Typing a different term holds for this test only until it is explicitly remembered.
    assert client.post(f"/author/{tid}/clinical-items/stress", json={
        "label": "Fatigue", "pattern": "Mitochondrial depletion",
    }).get_json() == {"ok": True, "remembered": False}
    page = client.get(f"/author/{tid}").data.decode()
    assert 'value="Mitochondrial depletion"' in page
    assert 'data-remembered="Adrenal exhaustion"' in page
    with sqlite3.connect(db) as cx:
        assert stress_pattern(cx, "Fatigue") == "Adrenal exhaustion"

    assert client.post(f"/author/{tid}/clinical-items/stress", json={
        "label": "Fatigue", "pattern": "Mitochondrial depletion", "replace": True,
    }).get_json() == {"ok": True, "remembered": True}
    with sqlite3.connect(db) as cx:
        assert stress_pattern(cx, "Fatigue") == "Mitochondrial depletion"


def test_a_pattern_only_row_does_not_clear_the_derived_remedy_ticks(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        save_pattern(cx, "a1", "Migraine", "Cerebral vascular spasm")
        rows = apply_selection(cx, "a1", [{"label": "Migraine", "covered_by": "Neuroprotect",
                                           "common_remedies": ["Neuroprotect"]}])
        assert rows[0]["stress_pattern"] == "Cerebral vascular spasm"
        assert "selection_saved" not in rows[0]


def test_the_replace_offer_survives_the_reload_that_hid_it(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        remember_stress_pattern(cx, "Fatigue", "Adrenal exhaustion")
    client = create_app(db, fetch_profile=lambda email: {"conditions": ["Fatigue"]}).test_client()

    page = client.get(f"/author/{tid}").data.decode()
    assert "class='btn ghost clinical-stress-save' hidden" in page

    client.post(f"/author/{tid}/clinical-items/stress",
                json={"label": "Fatigue", "pattern": "Mitochondrial depletion"})
    page = client.get(f"/author/{tid}").data.decode()
    assert "class='btn ghost clinical-stress-save' onclick=rememberClinicalStress" in page

    client.post(f"/author/{tid}/clinical-items/stress",
                json={"label": "Fatigue", "pattern": "Mitochondrial depletion", "replace": True})
    page = client.get(f"/author/{tid}").data.decode()
    assert "class='btn ghost clinical-stress-save' hidden" in page


def test_a_new_layer_after_imported_reveal_layers_keeps_the_number_it_promised(
        tmp_path, monkeypatch):
    """Import Reveal writes needs-review scan rows.  Adding a clinical item as
    'New layer 7' used to store layer 7 correctly but display it as layer 1, because
    the scan rows were forced to the end of the chain."""
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Rebecca", "r@example.com", "2026-09-02")
        for n, title in enumerate(
                ["Structural", "Cellular", "Lung", "Liver", "Cerebral", "Circulatory"], 1):
            add_chain_row(cx, tid, n, title, title, f"R{n}", confirmed=0, origin="scan")
    client = create_app(db, fetch_profile=lambda email: {"conditions": ["Dry Eye"]}).test_client()

    page = client.get(f"/author/{tid}").data.decode()
    assert "<option value='7'>New layer 7</option>" in page

    assert client.post(f"/author/{tid}/clinical-items/balance", json={
        "label": "Dry Eye", "layer": 7, "remedies": ["Moisturize"],
        "pattern": "Tear film instability",
    }).get_json()["ok"]

    with sqlite3.connect(db) as cx:
        cards = group_layers(ordered_chain(cx, tid))
    assert [c["layer"] for c in cards] == [1, 2, 3, 4, 5, 6, 7]
    assert cards[-1]["head"] == "Tear film instability"
    # The remedy stays inside its own layer instead of splitting into an eighth card.
    assert [(r.get("remedy") or "") for r in cards[-1]["rows"]] == ["", "Moisturize"]
    # The Reveal rows are still needs-review; they just are not forced to the end.
    assert [c["zone"] for c in cards] == ["bottom"] * 6 + ["top"]


def test_a_suggested_term_is_marked_and_a_recorded_one_is_not(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
    client = create_app(
        db, fetch_profile=lambda email: {"conditions": ["Sleep Difficulty"]}).test_client()

    page = client.get(f"/author/{tid}").data.decode()
    assert 'class=clinical-stress list=vocab value="Sleep Regulation"' in page
    assert "<span class=clinical-stress-hint>suggested</span>" in page
    # Nothing is recorded yet, so there is no standing term to offer to replace.
    assert 'data-remembered=""' in page
    assert "class='btn ghost clinical-stress-save' hidden" in page

    with sqlite3.connect(db) as cx:
        remember_stress_pattern(cx, "Sleep Difficulty", "Circadian Entrainment")
    page = client.get(f"/author/{tid}").data.decode()
    assert 'value="Circadian Entrainment"' in page
    # The badge is gone; only its (always-present) stylesheet rule remains.
    assert "<span class=clinical-stress-hint>suggested</span>" not in page


# ── A saved tick means checked ───────────────────────────────────────────────────
# Glen, 2026-09-18: "Add checked patterns -> Stresses doesn't seem to do anything."
# It did nothing because `checked` came only from build(): true when a remedy already
# on the causal chain covers the condition. Sharon Connour's chain was empty, so every
# item read unchecked however many he ticked, and the route reported checked: 0.
# His ruling: "a saved tick should mean checked".

def test_a_saved_tick_means_checked(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        save_selection(cx, "a1", "Fatigue", ["Adrenal Restore"])
        rows = apply_selection(cx, "a1", [{"label": "Fatigue", "covered_by": "",
                                           "checked": False, "common_remedies": []}])
        assert rows[0]["checked"] is True


def test_clearing_every_tick_unchecks_it(tmp_path):
    """A deliberately emptied selection wins outright, the rule apply_selection
    already applies to the remedies. Otherwise unticking would silently re-tick."""
    with sqlite3.connect(tmp_path / "x.db") as cx:
        save_selection(cx, "a1", "Migraine", ["Neuroprotect"])
        save_selection(cx, "a1", "Migraine", [])
        rows = apply_selection(cx, "a1", [{"label": "Migraine", "covered_by": "Neuroprotect",
                                           "checked": True, "common_remedies": []}])
        assert rows[0]["checked"] is False


def test_an_untouched_item_still_derives_checked_from_the_chain(tmp_path):
    with sqlite3.connect(tmp_path / "x.db") as cx:
        rows = apply_selection(cx, "a1", [{"label": "Migraine", "covered_by": "Neuroprotect",
                                           "checked": True, "common_remedies": []}])
        assert rows[0]["checked"] is True
        assert "selection_saved" not in rows[0]


# ── Layers from the Clinical Summary alone ───────────────────────────────────────
# Glen, 2026-09-18: "Add a button to create layers from the Clinical Summary checked
# patterns and remedies only. (Full and minimum still pull in the e4l layers as well)"
# So this one is deliberately narrower than the program buttons: no scan findings.

def test_each_checked_item_becomes_one_layer():
    from dashboard.biofield_clinical_checklist import clinical_layers
    items = [{"label": "Adrenal Fatigue", "checked": True, "stress_pattern": "Adrenal Support",
              "selected_remedies": ["Adrenal Syntropy"]},
             {"label": "Glaucoma suspect", "checked": True, "stress_pattern": "Ocular Flow",
              "selected_remedies": ["OcuFlow Bedtime", "OcuFlow Daytime"]}]
    got = clinical_layers(items)
    assert [L["pattern"] for L in got] == ["Adrenal Support", "Ocular Flow"]
    assert got[1]["remedies"] == ["OcuFlow Bedtime", "OcuFlow Daytime"]
    assert got[0]["label"] == "Adrenal Fatigue"


def test_an_unchecked_item_makes_no_layer():
    from dashboard.biofield_clinical_checklist import clinical_layers
    assert clinical_layers([{"label": "Migraine", "checked": False,
                             "stress_pattern": "Neuro Calm",
                             "selected_remedies": ["Neuroprotect"]}]) == []


def test_a_checked_item_with_no_pattern_makes_no_layer():
    """The chain speaks in stress patterns, never the client's own words. Guessing
    here would put 'my eyes hurt' in a causal chain -- the rule to-stresses follows."""
    from dashboard.biofield_clinical_checklist import clinical_layers
    assert clinical_layers([{"label": "my eyes hurt", "checked": True,
                             "stress_pattern": "", "selected_remedies": ["ACES"]}]) == []


def test_a_checked_item_with_a_pattern_but_no_remedy_still_makes_a_layer():
    """A layer may legitimately have no remedy yet; biofield_auth_layer_stress exists
    for exactly that. The pattern is what earns the layer."""
    from dashboard.biofield_clinical_checklist import clinical_layers
    got = clinical_layers([{"label": "Hot flashes", "checked": True,
                            "stress_pattern": "Endocrine Balance", "selected_remedies": []}])
    assert len(got) == 1 and got[0]["remedies"] == []


def test_two_checked_items_sharing_a_pattern_make_one_layer():
    """The same pattern twice is one layer, or the chain gains a duplicate root."""
    from dashboard.biofield_clinical_checklist import clinical_layers
    got = clinical_layers([
        {"label": "Hot flashes", "checked": True, "stress_pattern": "Endocrine Balance",
         "selected_remedies": ["Endocrine Restore"]},
        {"label": "Low progesterone", "checked": True, "stress_pattern": "Endocrine Balance",
         "selected_remedies": ["Vital Energy Be"]}])
    assert len(got) == 1
    assert got[0]["remedies"] == ["Endocrine Restore", "Vital Energy Be"]
    assert got[0]["label"] == "Hot flashes, Low progesterone"


def test_a_remedy_repeated_across_two_items_appears_once():
    from dashboard.biofield_clinical_checklist import clinical_layers
    got = clinical_layers([
        {"label": "A", "checked": True, "stress_pattern": "P", "selected_remedies": ["One"]},
        {"label": "B", "checked": True, "stress_pattern": "P", "selected_remedies": ["One"]}])
    assert got[0]["remedies"] == ["One"]


def _chain_count(db, tid):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM biofield_auth_chain WHERE test_id=?",
                          (int(str(tid).lstrip("a") or 0),)).fetchone()[0]


def _clinical_layers_app(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Sharon", "s@example.com", "2026-09-18")
        save_selection(cx, tid, "Fatigue", ["Adrenal Syntropy"])
        save_pattern(cx, tid, "Fatigue", "Adrenal Support")
    app = create_app(db, fetch_profile=lambda email: {"conditions": ["Fatigue"]},
                     fetch_recent_comms=lambda email: {})
    return db, tid, app.test_client()


def test_the_clinical_layers_button_proposes_without_writing(tmp_path, monkeypatch):
    db, tid, client = _clinical_layers_app(tmp_path, monkeypatch)
    j = client.post(f"/author/{tid}/clinical-items/layers", json={}).get_json()
    assert j["ok"] and j["proposed"] is True
    assert [L["pattern"] for L in j["layers"]] == ["Adrenal Support"]
    assert j["layers"][0]["remedies"] == ["Adrenal Syntropy"]
    assert _chain_count(db, tid) == 0, "the proposal wrote to the causal chain"


def test_the_clinical_layers_button_writes_on_apply(tmp_path, monkeypatch):
    db, tid, client = _clinical_layers_app(tmp_path, monkeypatch)
    j = client.post(f"/author/{tid}/clinical-items/layers",
                    json={"apply": True, "force": True}).get_json()
    assert j["ok"] and j["applied"] is True and j["layers_added"] == 1
    assert _chain_count(db, tid) == 1
    with sqlite3.connect(db) as cx:
        row = cx.execute("SELECT head, remedy, origin FROM biofield_auth_chain "
                         "WHERE test_id=?", (int(str(tid).lstrip("a") or 0),)).fetchone()
    assert row[0] == "Adrenal Support" and row[1] == "Adrenal Syntropy"


def test_the_clinical_layers_button_sees_no_scan_findings(tmp_path, monkeypatch):
    """Glen: this one is patterns and remedies from the Clinical Summary ONLY.
    Full and Minimum are the buttons that pull the E4L layers in as well."""
    db, tid, client = _clinical_layers_app(tmp_path, monkeypatch)
    from dashboard.biofield_stress import add_stress, init_stress_tables
    with sqlite3.connect(db) as cx:
        init_stress_tables(cx)
        add_stress(cx, tid, "ED12 Kidney Driver", source="scan", balance="required")
    j = client.post(f"/author/{tid}/clinical-items/layers", json={}).get_json()
    assert [L["pattern"] for L in j["layers"]] == ["Adrenal Support"]


def _hand_added_sleep(db, tid):
    """Alyssa Fukushima, test a40, 2026-09-22: Sleep Difficulty was ADDED by hand on
    the page, so it lives only as an accepted decision, never in the profile."""
    with sqlite3.connect(db) as cx:
        decide(cx, tid, "Sleep Difficulty", "accepted",
               "Manually added to clinical checklist")
        save_selection(cx, tid, "Sleep Difficulty", ["Sleep Syntropy"])
        remember_stress_pattern(cx, "Sleep Difficulty", "Sleep Regulation")


def test_the_clinical_layers_button_sees_a_hand_added_item(tmp_path, monkeypatch):
    """Glen, 2026-09-22: two checked items, one layer proposed. The page folds
    hand-added items into the checklist; the button rebuilt it without them."""
    db, tid, client = _clinical_layers_app(tmp_path, monkeypatch)
    _hand_added_sleep(db, tid)
    j = client.post(f"/author/{tid}/clinical-items/layers", json={}).get_json()
    assert j["checked"] == 2
    assert [L["pattern"] for L in j["layers"]] == ["Adrenal Support", "Sleep Regulation"]
    assert j["layers"][1]["remedies"] == ["Sleep Syntropy"]


def test_add_checked_patterns_sees_a_hand_added_item(tmp_path, monkeypatch):
    db, tid, client = _clinical_layers_app(tmp_path, monkeypatch)
    _hand_added_sleep(db, tid)
    j = client.post(f"/author/{tid}/clinical-items/to-stresses", json={}).get_json()
    assert j["checked"] == 2
    assert "Sleep Regulation" in j["added"]
