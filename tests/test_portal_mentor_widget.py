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
    for control in ("mentorInput","mentorSend","mentorMic","mentorSpeaker","mentorAutoGuide",
                    "mentorContinuous","mentorContinuousWrap"):
        assert f'id="{control}"' in PORTAL
    assert "window.SpeechRecognition||window.webkitSpeechRecognition" in MENTOR
    assert "SpeechSynthesisUtterance" in MENTOR

def test_continuous_voice_is_explicit_and_only_available_after_both_channels_activate():
    assert "continuousWrap.hidden=!available" in MENTOR
    assert "recognition&&micActivated&&speakerOn" in MENTOR
    assert "if(continuousOn)submit()" in MENTOR
    assert "u.onend=u.onerror" in MENTOR
    assert "scheduleListening()" in MENTOR

def test_continuous_voice_recovers_from_transient_recognition_shutdowns():
    assert "recognitionStarting" in MENTOR
    assert "recognitionFatal" in MENTOR
    assert "Math.min(250*Math.pow(2,restartAttempts++),4000)" in MENTOR
    assert "catch(e){recognitionStarting=false;scheduleListening(true)}" in MENTOR
    assert "else if(continuousOn){scheduleListening(true)}" in MENTOR
    assert "if(continuousOn)scheduleListening();else paintMicActive(false)" in MENTOR
    assert "if(continuousOn&&!speaking)scheduleListening()" in MENTOR

def test_continuous_voice_is_never_remembered_and_resets_when_mentor_closes():
    assert 'localStorage.setItem("rm_mentor_continuous"' not in MENTOR
    assert 'localStorage.getItem("rm_mentor_continuous"' not in MENTOR
    assert "function closeMentor(){" in MENTOR
    assert "disableContinuous();if(listening)" in MENTOR

def test_background_portal_never_speaks_or_listens_and_auto_guidance_is_visual_first():
    assert "if(document.hidden)return;" in MENTOR
    # document.hidden is only about the browser tab. At page load, before the
    # payload arrives, the mentor can be bound to a surface that is about to be
    # replaced, and under the portal shell the card sits in a [data-panel] section
    # that is hidden at every other door. A host the client cannot see must not
    # speak either. This is an ADDITIONAL condition; the tab check above stays.
    assert "if(hostHidden())return;" in MENTOR
    assert 'document.addEventListener("visibilitychange"' in MENTOR
    assert 'window.addEventListener("pagehide",silenceHiddenMentor)' in MENTOR
    # Task 7 (portal-shell-ia) generalised "is the mentor open?" to "is the host the
    # mentor is bound to visible?", because under the portal shell the host is the
    # chat card, not the floating panel. The guarantee is unchanged and now covers
    # both hosts, so the pin is widened rather than dropped: hostHidden() must still
    # answer with panel.hidden for the floating panel, and the panel must still be
    # opened (visually, before anything is spoken) when guidance arrives closed.
    assert "const wasOpen=!hostHidden()" in MENTOR
    assert "if(!h.card)return !!h.panel.hidden;" in MENTOR
    assert "if(!h.card&&h.panel.hidden)openMentor(false)" in MENTOR
    assert "if(wasOpen&&!document.hidden)speak(text)" in MENTOR

def test_widget_reuses_persistent_chat_and_supplies_page_context():
    assert 'fetch("/api/portal/"+encodeURIComponent(token)+"/chat"' in MENTOR
    assert "page_context:pageContext()" in MENTOR
    assert "window.syncMentorHistory" in MENTOR

def test_backend_bounds_context_and_prevents_false_observation():
    assert 'raw_page = data.get("page_context") or {}' in APP
    assert 'headings[:8]' in APP
    assert "Do not claim they clicked, read, or completed anything" in APP
