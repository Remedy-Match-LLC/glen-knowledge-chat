import os
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import db


def _seed(path, *, master_ready, group_ready, exposed_group_link=False):
    start = (datetime.now(ZoneInfo("Pacific/Honolulu")) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0, tzinfo=None)
    with db.connect(str(path)) as cx:
        cx.execute("CREATE TABLE masterclass_events (id INTEGER, topic TEXT, description TEXT, "
                   "start_ts TEXT, duration_min INTEGER, price_cents INTEGER, "
                   "member_price_cents INTEGER, zoom_join_url TEXT, zoom_meeting_id TEXT, "
                   "registration_required INTEGER)")
        cx.execute("CREATE TABLE calendar_events (id INTEGER, summary TEXT, start TEXT, "
                   '"end" TEXT, location TEXT, status TEXT, calendar_name TEXT, '
                   "zoom_meeting_id TEXT, zoom_occurrence_id TEXT, "
                   "zoom_registration_url TEXT, zoom_registration_required INTEGER)")
        cx.execute("INSERT INTO masterclass_events VALUES "
                   "(1,'Free Wellness Whispering MasterClass','Free live class',?,60,0,0,'',?,?)",
                   ((start + timedelta(hours=1)).isoformat(),
                    "22222222222" if master_ready else "", 1 if master_ready else 0))
        cx.execute("INSERT INTO calendar_events VALUES "
                   "(2,'Group Coaching',?,?,?,'visible','Group Coaching',?,'','',?)",
                   (start.isoformat(), (start + timedelta(hours=1)).isoformat(),
                    "https://zoom.test/shared" if exposed_group_link else "Zoom",
                    "11111111111" if group_ready else "", 1 if group_ready else 0))
        cx.commit()


def test_health_endpoint_checks_occurrences_and_links(monkeypatch, tmp_path):
    path = tmp_path / "healthy.db"
    _seed(path, master_ready=True, group_ready=True)
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    response = appmod.app.test_client().get(
        "/api/console/community-live-health", headers={"X-Console-Key": "secret"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["future_masterclasses"] == 1
    assert body["future_group_coaching"] == 1


def test_health_endpoint_reports_missing_zoom_links(monkeypatch, tmp_path):
    path = tmp_path / "unhealthy.db"
    _seed(path, master_ready=False, group_ready=False)
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    body = appmod.app.test_client().get(
        "/api/console/community-live-health", headers={"X-Console-Key": "secret"}).get_json()
    assert body["ok"] is False
    assert "Group Coaching private Zoom registration missing" in body["issues"]
    assert "MasterClass private Zoom registration missing" in body["issues"]


def test_health_endpoint_rejects_shared_group_zoom_location(monkeypatch, tmp_path):
    path = tmp_path / "exposed.db"
    _seed(path, master_ready=True, group_ready=True, exposed_group_link=True)
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    body = appmod.app.test_client().get(
        "/api/console/community-live-health", headers={"X-Console-Key": "secret"}).get_json()
    assert body["ok"] is False
    assert "Group Coaching shared Zoom link exposed" in body["issues"]


def test_bootstrap_creates_distinct_registration_required_occurrences(monkeypatch, tmp_path):
    path = tmp_path / "bootstrap.db"
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "test-client")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("dashboard.zoom.get_token", lambda *a, **k: "token")
    calls = []
    def fake_create(token, **kwargs):
        calls.append(kwargs)
        meeting_id = "11111111111" if kwargs["topic"] == "Group Coaching" else "22222222222"
        return {"meeting_id": meeting_id, "join_url": f"https://zoom.test/j/{meeting_id}",
                "registration_url": f"https://zoom.test/register/{meeting_id}",
                "start_url": ""}
    monkeypatch.setattr("dashboard.zoom.create_meeting", fake_create)
    client = appmod.app.test_client()
    first = client.post(
        "/api/console/community-live/bootstrap", headers={"X-Console-Key": "secret"})
    assert first.status_code == 200
    assert first.get_json()["created"] == ["group_coaching", "masterclass"]
    assert len(calls) == 2
    assert all(call["registration_required"] is True for call in calls)
    assert all("recurrence" not in call for call in calls)
    with db.connect(str(path)) as cx:
        group = cx.execute(
            "SELECT location,zoom_meeting_id,zoom_registration_required FROM calendar_events"
        ).fetchone()
        master = cx.execute(
            "SELECT zoom_join_url,zoom_meeting_id,registration_required FROM masterclass_events"
        ).fetchone()
    assert tuple(group) == ("Zoom", "11111111111", 1)
    assert tuple(master) == ("", "22222222222", 1)
    second = client.post(
        "/api/console/community-live/bootstrap", headers={"X-Console-Key": "secret"})
    assert second.get_json()["created"] == []
    assert len(calls) == 2
