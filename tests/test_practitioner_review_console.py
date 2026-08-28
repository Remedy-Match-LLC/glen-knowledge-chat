"""Console review queue for practitioner profile drafts."""
import os

import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "s3cret")
    monkeypatch.setitem(appmod.app.config, "TESTING", True)
    return appmod.app.test_client()


def test_queue_requires_the_console_key(client):
    assert client.get("/api/console/practitioner-drafts").status_code == 401


def test_approve_requires_the_console_key(client):
    r = client.post(f"/api/console/practitioner-drafts/{PID}/approve")
    assert r.status_code == 401


def test_reject_requires_the_console_key(client):
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject",
                    json={"note": "no"})
    assert r.status_code == 401


def test_queue_lists_submitted_drafts(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr("dashboard.practitioner_drafts.list_by_status",
                        lambda cx, status=None, limit=200: [
                            {"practitioner_id": PID, "status": "submitted",
                             "fields": {"bio": "x"}}])
    r = client.get("/api/console/practitioner-drafts")
    assert r.status_code == 200
    assert r.get_json()["drafts"][0]["practitioner_id"] == PID


def test_reject_without_a_note_is_a_400(client, monkeypatch):
    monkeypatch.setattr(appmod, "_console_key_ok", lambda: True)
    r = client.post(f"/api/console/practitioner-drafts/{PID}/reject", json={})
    assert r.status_code == 400
