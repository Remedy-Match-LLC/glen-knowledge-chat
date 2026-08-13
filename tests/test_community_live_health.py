import os
import pytest

if not os.environ.get("PINECONE_API_KEY"):
    pytest.skip("needs doppler env for import app", allow_module_level=True)

import app as appmod
from dashboard import db


def _seed(path, *, master_url, group_url):
    with db.connect(str(path)) as cx:
        cx.execute("CREATE TABLE masterclass_events (id INTEGER, topic TEXT, description TEXT, "
                   "start_ts TEXT, duration_min INTEGER, zoom_join_url TEXT)")
        cx.execute("CREATE TABLE calendar_events (id INTEGER, summary TEXT, start TEXT, "
                   '"end" TEXT, location TEXT, status TEXT, calendar_name TEXT)')
        cx.execute("INSERT INTO masterclass_events VALUES "
                   "(1,'Free Wellness Whispering MasterClass','Free live class',"
                   "'2026-08-12T15:00:00',60,?)", (master_url,))
        cx.execute("INSERT INTO calendar_events VALUES "
                   "(2,'Group Coaching','2026-08-12T13:00:00','2026-08-12T14:00:00',"
                   "?,'visible','Group Coaching')", (group_url,))
        cx.commit()


def test_health_endpoint_checks_occurrences_and_links(monkeypatch, tmp_path):
    path = tmp_path / "healthy.db"
    _seed(path, master_url="https://zoom.test/master", group_url="https://zoom.test/group")
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    response = appmod.app.test_client().get(
        "/api/console/community-live-health", headers={"X-Console-Key": "secret"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["future_masterclasses"] == 8
    assert body["future_group_coaching"] == 8


def test_health_endpoint_reports_missing_zoom_links(monkeypatch, tmp_path):
    path = tmp_path / "unhealthy.db"
    _seed(path, master_url="", group_url="")
    monkeypatch.setattr(appmod, "LOG_DB", str(path))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "secret")
    body = appmod.app.test_client().get(
        "/api/console/community-live-health", headers={"X-Console-Key": "secret"}).get_json()
    assert body["ok"] is False
    assert "Group Coaching Zoom link missing" in body["issues"]
    assert "MasterClass Zoom link missing" in body["issues"]
