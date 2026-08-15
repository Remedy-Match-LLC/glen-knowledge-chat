"""Static contracts for the persistent, page-aware portal mentor."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = (ROOT / "static" / "client-portal.html").read_text()
MENTOR = (ROOT / "static" / "portal-mentor.js").read_text()
APP = (ROOT / "app.py").read_text()

def test_widget_is_persistent_and_expandable():
    assert 'id="mentorLauncher"' in PORTAL and 'id="mentorPanel"' in PORTAL
    assert ".mentor-launcher{position:fixed" in PORTAL
    assert 'aria-label="Open Mentorship University mentor"' in PORTAL
    assert '<script src="/static/portal-mentor.js"></script>' in PORTAL

def test_widget_has_text_voice_and_guidance_controls():
    for control in ("mentorInput","mentorSend","mentorMic","mentorSpeaker","mentorAutoGuide"):
        assert f'id="{control}"' in PORTAL
    assert "window.SpeechRecognition||window.webkitSpeechRecognition" in MENTOR
    assert "SpeechSynthesisUtterance" in MENTOR

def test_widget_reuses_persistent_chat_and_supplies_page_context():
    assert 'fetch("/api/portal/"+encodeURIComponent(token)+"/chat"' in MENTOR
    assert "page_context:pageContext()" in MENTOR
    assert "window.syncMentorHistory" in MENTOR

def test_backend_bounds_context_and_prevents_false_observation():
    assert 'raw_page = data.get("page_context") or {}' in APP
    assert 'headings[:8]' in APP
    assert "Do not claim they clicked, read, or completed anything" in APP
