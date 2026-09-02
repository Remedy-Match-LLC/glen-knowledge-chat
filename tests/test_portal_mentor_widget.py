"""Static contracts for the persistent, page-aware portal mentor."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = (ROOT / "static" / "client-portal.html").read_text()
MENTOR = (ROOT / "static" / "portal-mentor.js").read_text()
APP = (ROOT / "app.py").read_text()


def _live(src: str, marker: str = "//") -> str:
    """The source with whole-line comments removed.

    Final review I8: every assertion in this file was a bare substring check
    against the raw file, so commenting out each pinned line in
    portal-mentor.js left all of them green. A comment naming a call satisfies a
    substring check for that call, which is the opposite of what a pin is for.

    Line-based on purpose: it removes exactly the shape a commented-out line
    takes, and cannot corrupt a string that merely contains the marker, such as a
    URL, the way an index-based strip would. The marker is a parameter because
    app.py comments with "#" while the page and the widget comment with "//", and
    a "#" stripper over the page would eat its CSS id rules.
    """
    return "\n".join(
        l for l in src.split("\n") if not l.strip().startswith(marker))


# Assertions about what the code DOES run against these. The raw text is kept for
# the few that assert something is ABSENT, where the raw file is the stricter
# check: a pinned absence should fail even if the line only survives in a comment.
PORTAL_CODE = _live(PORTAL)
MENTOR_CODE = _live(MENTOR)
APP_CODE = _live(APP, "#")   # app.py comments with #, not //

def test_widget_is_persistent_and_expandable():
    assert 'id="mentorLauncher"' in PORTAL_CODE and 'id="mentorPanel"' in PORTAL_CODE
    assert ".mentor-launcher{position:fixed" in PORTAL_CODE
    assert 'aria-label="Open Mentorship University mentor"' in PORTAL_CODE
    assert '<script src="/static/portal-mentor.js"></script>' in PORTAL_CODE

def test_widget_has_text_voice_and_guidance_controls():
    for control in ("mentorInput","mentorSend","mentorMic","mentorSpeaker","mentorAutoGuide",
                    "mentorContinuous","mentorContinuousWrap"):
        assert f'id="{control}"' in PORTAL_CODE
    assert "window.SpeechRecognition||window.webkitSpeechRecognition" in MENTOR_CODE
    assert "SpeechSynthesisUtterance" in MENTOR_CODE

def test_continuous_voice_is_explicit_and_only_available_after_both_channels_activate():
    assert "continuousWrap.hidden=!available" in MENTOR_CODE
    assert "recognition&&micActivated&&speakerOn" in MENTOR_CODE
    assert "if(continuousOn)submit()" in MENTOR_CODE
    assert "u.onend=u.onerror" in MENTOR_CODE
    assert "scheduleListening()" in MENTOR_CODE

def test_continuous_voice_recovers_from_transient_recognition_shutdowns():
    assert "recognitionStarting" in MENTOR_CODE
    assert "recognitionFatal" in MENTOR_CODE
    assert "Math.min(250*Math.pow(2,restartAttempts++),4000)" in MENTOR_CODE
    assert "catch(e){recognitionStarting=false;scheduleListening(true)}" in MENTOR_CODE
    assert "else if(continuousOn){scheduleListening(true)}" in MENTOR_CODE
    assert "if(continuousOn)scheduleListening();else paintMicActive(false)" in MENTOR_CODE
    assert "if(continuousOn&&!speaking)scheduleListening()" in MENTOR_CODE

def test_continuous_voice_is_never_remembered_and_resets_when_mentor_closes():
    assert 'localStorage.setItem("rm_mentor_continuous"' not in MENTOR
    assert 'localStorage.getItem("rm_mentor_continuous"' not in MENTOR
    assert "function closeMentor(){" in MENTOR_CODE
    assert "disableContinuous();if(listening)" in MENTOR_CODE

def test_background_portal_never_speaks_or_listens_and_auto_guidance_is_visual_first():
    assert "if(document.hidden)return;" in MENTOR_CODE
    # document.hidden is only about the browser tab. At page load, before the
    # payload arrives, the mentor can be bound to a surface that is about to be
    # replaced, and under the portal shell the card sits in a [data-panel] section
    # that is hidden at every other door. A host the client cannot see must not
    # speak either. This is an ADDITIONAL condition; the tab check above stays.
    assert "if(hostHidden())return;" in MENTOR_CODE
    assert 'document.addEventListener("visibilitychange"' in MENTOR_CODE
    assert 'window.addEventListener("pagehide",silenceHiddenMentor)' in MENTOR_CODE
    # Task 7 (portal-shell-ia) generalised "is the mentor open?" to "is the host the
    # mentor is bound to visible?", because under the portal shell the host is the
    # chat card, not the floating panel. The guarantee is unchanged and now covers
    # both hosts, so the pin is widened rather than dropped: hostHidden() must still
    # answer with panel.hidden for the floating panel, and the panel must still be
    # opened (visually, before anything is spoken) when guidance arrives closed.
    assert "const wasOpen=!hostHidden()" in MENTOR_CODE
    assert "if(!h.card)return !!h.panel.hidden;" in MENTOR_CODE
    assert "if(!h.card&&h.panel.hidden)openMentor(false)" in MENTOR_CODE
    assert "if(wasOpen&&!document.hidden)speak(text)" in MENTOR_CODE

def test_widget_reuses_persistent_chat_and_supplies_page_context():
    assert 'fetch("/api/portal/"+encodeURIComponent(token)+"/chat"' in MENTOR_CODE
    assert "page_context:pageContext()" in MENTOR_CODE
    assert "window.syncMentorHistory" in MENTOR_CODE

def test_backend_bounds_context_and_prevents_false_observation():
    assert 'raw_page = data.get("page_context") or {}' in APP_CODE
    assert 'headings[:8]' in APP_CODE
    assert "Do not claim they clicked, read, or completed anything" in APP_CODE
