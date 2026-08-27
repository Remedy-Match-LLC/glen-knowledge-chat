import importlib
import sqlite3
import sys
from pathlib import Path

from dashboard import historical_intakes as hi


def _app(tmp_path, monkeypatch, console_secret="secret"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_HUB_ENABLED", "1")
    monkeypatch.setenv("PORTAL_HEALTH_PROFILE_ENABLED", "1")
    monkeypatch.setenv("CONSOLE_SECRET", console_secret)
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    return appmod


def _seed(appmod, email, *, visible=False, source_id="pb-1"):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        token = cp.ensure_token(cx, email, "Client")
        snapshot_id = hi.put_import(
            cx, person_id=7, email=email, form_date="2025-01-02",
            form_name="Practice Better Intake",
            answers={"terrain": 2, "sleep": "Woke often", "first_name": "Private",
                     "terms": {"agreed": True, "signature": "Must not survive"}},
            source_system="practice-better", source_record_id=source_id,
            import_batch_id="pilot-1")
        if visible:
            hi.review(cx, snapshot_id, approved=True, visible=True, reviewed_by="Glen")
    return token, snapshot_id


def test_store_is_idempotent_immutable_and_discards_consent():
    cx = sqlite3.connect(":memory:")
    first = hi.put_import(
        cx, person_id=1, email="A@X.com", form_date="2025-01-01", form_name="Intake",
        answers={"sleep": "old", "terms": {"agreed": True}},
        source_system="practice-better", source_record_id="same")
    second = hi.put_import(
        cx, person_id=1, email="a@x.com", form_date="2026-01-01", form_name="Changed",
        answers={"sleep": "replacement"}, source_system="practice-better",
        source_record_id="same")
    assert first == second
    row = hi.list_for_email(cx, "a@x.com")[0]
    assert row["form_date"] == "2025-01-01"
    assert row["answers"] == {"sleep": "old"}
    assert row["client_visible"] is False


def test_unreviewed_snapshot_is_invisible_to_portal(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, _ = _seed(appmod, "client@example.com")
    body = appmod.app.test_client().get(
        f"/api/portal/{token}/clinical-record/intakes").get_json()
    assert body["items"] == []


def test_approved_snapshot_exposes_only_curated_health_fields(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, _ = _seed(appmod, "client@example.com", visible=True)
    body = appmod.app.test_client().get(
        f"/api/portal/{token}/clinical-record/intakes").get_json()
    assert len(body["items"]) == 1
    fields = {field["id"]: field for field in body["items"][0]["fields"]}
    assert set(fields) == {"terrain", "sleep"}
    assert fields["terrain"]["value"] == "Rapid Aging, Bacterial, or Parasitic"
    raw = str(body)
    assert "signature" not in raw and "first_name" not in raw and "source_record_id" not in raw


def test_copy_selected_updates_current_profile_and_never_consent(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, snapshot_id = _seed(appmod, "client@example.com", visible=True)
    response = appmod.app.test_client().post(
        f"/api/portal/{token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["terrain", "sleep"]})
    assert response.status_code == 200
    assert response.get_json()["copied_fields"] == ["sleep", "terrain"]
    from dashboard import intake
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        answers = intake.get_response(cx, "client@example.com")["answers"]
    assert answers["terrain"] == 2 and answers["sleep"] == "Woke often"
    assert "terms" not in answers


def test_copy_rejects_identity_field_and_cross_token_access(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    owner_token, snapshot_id = _seed(appmod, "owner@example.com", visible=True)
    other_token, _ = _seed(appmod, "other@example.com", visible=True, source_id="pb-2")
    client = appmod.app.test_client()
    assert client.post(
        f"/api/portal/{owner_token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["first_name"]}).status_code == 400
    assert client.post(
        f"/api/portal/{other_token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["sleep"]}).status_code == 404


def test_console_review_requires_auth_and_rejection_forces_hidden(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    _, snapshot_id = _seed(appmod, "client@example.com")
    client = appmod.app.test_client()
    route = f"/api/console/historical-intake/{snapshot_id}/review"
    assert client.post(route, json={"approved": True, "visible": True}).status_code == 401
    response = client.post(route, json={"approved": False, "visible": True},
                           headers={"X-Console-Key": "secret"})
    assert response.status_code == 200
    assert response.get_json()["client_visible"] is False
    with sqlite3.connect(appmod.LOG_DB) as cx:
        row = hi.list_for_email(cx, "client@example.com")[0]
    assert row["review_status"] == "rejected" and row["client_visible"] is False
