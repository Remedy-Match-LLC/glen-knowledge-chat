import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import create_test, init_auth_tables
from dashboard.biofield_clinical_proposals import (
    accepted_labels, decide, decisions, dismissed_labels, proposals,
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
