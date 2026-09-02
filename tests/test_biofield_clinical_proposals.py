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
