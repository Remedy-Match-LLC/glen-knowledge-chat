import importlib
import io
import sqlite3
import sys
from pathlib import Path

import pytest


def _app(monkeypatch, tmp_db):
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        app = importlib.import_module("app")
    except Exception as exc:
        pytest.skip(f"app module not importable: {exc}")
    monkeypatch.setattr(app, "LOG_DB", str(tmp_db))
    app._init_workspace_schema()
    return app


def _seed_portal(tmp_db):
    from dashboard import client_portal
    with sqlite3.connect(str(tmp_db)) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(
            cx, "voice@example.com", "Voice Member", {"biofield_status": "none"}
        )
    return token


def test_voice_analysis_is_token_scoped_and_persists_member_state(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    import journal_blueprint
    from dashboard import tcm_analysis

    temp_paths = []

    def fake_whisper(path):
        temp_paths.append(Path(path))
        assert Path(path).exists()
        return {
            "text": "I have felt worried and heavy, but I am ready to move forward.",
            "words": [
                {"word": "I", "start": 0.0, "end": 0.2},
                {"word": "have", "start": 0.3, "end": 0.5},
            ],
        }

    monkeypatch.setattr(journal_blueprint, "_whisper_transcribe", fake_whisper)
    monkeypatch.setattr(tcm_analysis, "_haiku_analyze", lambda transcript, lexical: {
        "elements": {"Wood": 20, "Fire": 10, "Earth": 40, "Metal": 20, "Water": 10},
        "emotions": {"Contemplation": .8, "Determination": .5},
        "top_themes": ["carrying responsibility", "moving forward"],
    })

    response = app.app.test_client().post(
        f"/api/portal/{token}/five-element-voice",
        data={
            "duration_seconds": "30",
            "audio": (io.BytesIO(b"fake webm bytes"), "voice.webm"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["dominant_element"] == "Earth"
    assert body["element_to_nourish"] in {"Fire", "Water"}
    assert body["transcript"].startswith("I have felt worried")
    assert temp_paths and not temp_paths[0].exists()

    from dashboard import member_element_state
    with sqlite3.connect(str(tmp_db)) as cx:
        saved = member_element_state.get(cx, "voice@example.com")
    assert saved["source"] == "portal_voice"
    assert saved["element_scores"]["Earth"] == 40


def test_voice_analysis_rejects_bad_token_before_processing_audio(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    response = app.app.test_client().post(
        "/api/portal/not-a-token/five-element-voice",
        data={"duration_seconds": "30", "audio": (io.BytesIO(b"x"), "voice.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_voice_analysis_rejects_invalid_duration(monkeypatch, tmp_db):
    app = _app(monkeypatch, tmp_db)
    token = _seed_portal(tmp_db)
    response = app.app.test_client().post(
        f"/api/portal/{token}/five-element-voice",
        data={"duration_seconds": "forever", "audio": (io.BytesIO(b"x"), "voice.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid recording duration"


def test_portal_template_contains_voice_card_and_hub_tile():
    html = (Path(__file__).resolve().parent.parent / "static" / "client-portal.html").read_text()
    assert '"voice", "5-Element Voice"' in html
    assert 'id="fiveElementVoiceCard"' in html
    assert "five-element-voice`" in html
    assert "navigator.mediaDevices.getUserMedia" in html
